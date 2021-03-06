from .base import Gate, GateError
from .utilities import multi_centroid_calculation, inside_polygon, centroid
from multiprocessing import Pool, cpu_count
from sklearn.cluster import DBSCAN, KMeans
from sklearn.neighbors import KDTree, KNeighborsClassifier
from shapely.geometry import Point
from functools import partial
import pandas as pd
import numpy as np
import collections
import hdbscan


def _meta_assignment_df(label: str or int, ref_df: pd.DataFrame) -> str or int:
    ref_df = ref_df.copy()
    if label == -1:
        return -1
    ref_df = ref_df[ref_df['cluster'] == label]
    assert ref_df.shape[0] > 0, f'No associated meta-cluster for {label}'
    assert ref_df.shape[0] == 1, f'More than one meta-cluster found for {label}'
    return ref_df['meta_cluster'].values[0]


def meta_assignment(df: pd.DataFrame, meta_clusters: pd.DataFrame) -> pd.DataFrame:
    """
    Given a reference dataframe of meta-cluster assignments, update the target dataframes cluster
    assignments
    :param df: Target dataframe for updating
    :param meta_clusters: Reference dataframe of meta clusters as generated from self._meta_clustering
    :return: Updated dataframe
    """
    df = df.copy()
    ref_df = meta_clusters[meta_clusters['chunk_idx'] == df['chunk_idx'].values[0]]
    df['labels'] = df['labels'].apply(lambda x: _meta_assignment_df(label=x, ref_df=ref_df))
    return df


class DensityClustering(Gate):
    """
    Class for Density based spatial clustering for applications with noise. Implements both DBSCAN and HDBSCAN

    Parameters
    ----------
    min_pop_size: int
        The minimum number of samples ("total weight") in a neighborhood for a point to be considered a core point.
        (see Scikit-Learn user guide and https://hdbscan.readthedocs.io/en/latest/how_hdbscan_works.html for details)
    tree: KDTree
        Nearest neighbours tree used for associating target population to cluster
    """
    def __init__(self,
                 min_pop_size: int,
                 **kwargs):
        super().__init__(**kwargs)
        self.min_pop_size = min_pop_size
        self.tree = KDTree(self.data[[self.x, self.y]].values, leaf_size=100)

    def _meta_clustering(self, clustered_chunks: list):
        """
        Given a list of clustered samples, fit a KMeans model to find 'meta-clusters' that will allow for merging
        of samples into a single dataframe

        Parameters
        -----------

        clustered_chunks: list
            list of pandas dataframes, each clustered using DBSCAN and cluster labels assigned
            to a column named 'labels'

        Returns
        --------
        Pandas.DataFrame
            reference dataframe that assigns each sample cluster to a meta-cluster
        """
        # Calculate K (number of meta clusters) as the number of expected populations OR the median number of
        # clusters found in each sample IF this median is less that the number of expected populations
        clustered_chunks_wo_noise = [x[x['labels'] != -1] for x in clustered_chunks]
        median_k = np.median([len(x['labels'].unique()) for x in clustered_chunks_wo_noise])
        if median_k < len(self.child_populations.populations.keys()):
            k = int(median_k)
        else:
            k = len(self.child_populations.populations.keys())

        # Calculate centroids of each cluster, this will be used as the training data
        pool = Pool(cpu_count())
        cluster_centroids = pd.concat(pool.map(multi_centroid_calculation, clustered_chunks_wo_noise))
        pool.close()
        pool.join()
        meta = KMeans(n_clusters=k, n_init=10, precompute_distances=True, random_state=42, n_jobs=-1)
        meta.fit(cluster_centroids[['x', 'y']].values)
        cluster_centroids['meta_cluster'] = meta.labels_
        return cluster_centroids

    def _dbscan_chunks(self,
                       distance_nn: float,
                       core_only: bool):
        """
        Perform DBSCAN across 'chunks' of original data set and perform meta-clustering to form a consensus.

        Parameters
        ----------
        distance_nn: float
            The maximum distance between two samples for one to be considered as in the neighborhood of the other.
            (see http://scikit-learn.org/stable/modules/generated/sklearn.cluster.DBSCAN.html)
        core_only: bool
            If True, only data-points considered 'core samples' are kept
            (see https://scikit-learn.org/stable/modules/clustering.html#dbscan)

        Returns
        -------
        Pandas.DataFrame, dict
            Meta-clustering results and polygon objects for each cluster
        """
        # Break data into workable chunks
        chunks = self.generate_chunks(30000)

        # Cluster each chunk!
        clustered_data = list()
        for sample in chunks:
            model = DBSCAN(eps=distance_nn,
                           min_samples=self.min_pop_size,
                           algorithm='ball_tree',
                           n_jobs=-1)
            model.fit(sample[[self.x, self.y]])
            db_labels = model.labels_
            if core_only:
                non_core_mask = np.ones(len(db_labels), np.bool)
                non_core_mask[model.core_sample_indices_] = 0
                np.put(db_labels, non_core_mask, -1)
            sample['labels'] = db_labels
            clustered_data.append(sample)

        # Perform meta clustering and merge clusters across samples
        meta_clusters = self._meta_clustering(clustered_data)
        pool = Pool(cpu_count())
        f = partial(meta_assignment, meta_clusters=meta_clusters)
        data = pd.concat(pool.map(f, clustered_data))
        pool.close()
        pool.join()
        data = self._post_cluster_checks(data)

        # Generate a Polygon for each cluster and update data according to polygon gates
        polygon_shapes = self.generate_polygons()
        for cluster_name, poly in polygon_shapes.items():
            mask = inside_polygon(data, self.x, self.y, poly)
            label_mask = data.index.isin(mask.index)
            data['labels'] = data['labels'].mask(label_mask, cluster_name)

        return data, polygon_shapes

    def _post_cluster_checks(self, data):
        """
        Internal function. Check validity of clustering results: have distinct populations been found? Does the number
        of clusters match the number of expected populations?

        Parameters
        ----------
        data: Pandas.DataFrame
            Meta-clustering results

        Returns
        -------
        Pandas.DataFrame
            Meta-clustering results
        """
        # Post clustering error checking
        if len(data['labels'].unique()) == 1:
            self.warnings.append('Failed to identify any distinct populations')
            return self.child_populations

        n_children = len(self.child_populations.populations.keys())
        n_clusters = len(data['labels'].unique())
        if n_clusters != n_children:
            self.warnings.append(f'Expected {n_children} populations, identified {n_clusters}')
        return data

    def _dbscan_knn(self, distance_nn: float, core_only):
        """
        Perform DBSCAN clustering on a sample of the target data. Train a KNN classifier on the clustering results
        and then predict the cluster assignment for remaining data. Clustering results assigned to column named
        'labels'.

        Parameters
        ----------
        distance_nn: float
            Maximum distance between two samples for one to be considered as in the neighborhood of the other
            (see https://scikit-learn.org/stable/modules/clustering.html#dbscan)

        core_only: bool
            If True, only data-points considered 'core samples' are kept
            (see https://scikit-learn.org/stable/modules/clustering.html#dbscan)

        Returns
        -------
        None
        """

        sample = self.sampling(self.data, 40000)
        if sample is None:
            print('Warning: no value given for frac, downsampling is recommended prior to fitting model')
            sample = self.data
        model = DBSCAN(eps=distance_nn,
                       min_samples=self.min_pop_size,
                       algorithm='ball_tree',
                       n_jobs=-1)
        model.fit(sample[[self.x, self.y]])
        db_labels = model.labels_
        if core_only:
            non_core_mask = np.ones(len(db_labels), np.bool)
            non_core_mask[model.core_sample_indices_] = 0
            np.put(db_labels, non_core_mask, -1)

        knn = KNeighborsClassifier(n_neighbors=10,
                                   weights='distance',
                                   algorithm='ball_tree').fit(sample[[self.x, self.y]], db_labels)
        self.data['labels'] = knn.predict(self.data[[self.x, self.y]])

    def dbscan(self, distance_nn: int or float, core_only: bool = False, chunks: bool =  False):
        """
        Perform gating with Scikit-Learn implementation of DBSCAN algorithm
        (https://scikit-learn.org/stable/modules/generated/sklearn.cluster.DBSCAN.html)

        DBSCAN clustering is performed on a sample of the original data. The results this clustering are used
        to train a KNN classifier. Remaining data is then assigned to clusters according to their proximity to clusters.
        Once clusters have been established, target populations are associated to clusters according to how close
        their approximated centroid is to cluster centroids.

        Parameters
        -----------
        distance_nn: float
            The maximum distance between neighbours for them to be considered part of the same cluster
            (smaller value will create tighter clusters)
        core_only: bool
            if True, only core samples in density clusters will be included (see Scikit-learn documentation)

        Returns
        --------
        ChildPopulationCollection
            Updated child populations with events indexing complete
        """
        # If parent is empty just return the child populations with empty index array
        if self.empty_parent:
            return self.child_populations
        if chunks:
            self.data, polygon_shapes = self._dbscan_chunks(distance_nn, core_only)
        else:
            self._dbscan_knn(distance_nn, core_only)
            polygon_shapes = self.generate_polygons()
        # Test if target population falls within polygon and if so, assign accordingly
        target_predictions = self._predict_pop_clusters(polygon_shapes)

        # Update child populations
        return self._assign_clusters(target_predictions, polygon_shapes)

    def hdbscan(self,
                inclusion_threshold: float or None = None):
        """
        Perform gating with HDBSCAN algorithm
        (https://hdbscan.readthedocs.io/en/latest/how_hdbscan_works.html)

        HDBSCAN clustering is performed on either the whole dataset or a sample of the dataset if specified.
        If clustering is performed on a sample, a call to 'approximate_predict' is made for remaining data.
        (https://hdbscan.readthedocs.io/en/latest/api.html#hdbscan.prediction.approximate_predict)

        Parameters
        -----------
        inclusion_threshold: float, optional
            float value for minimum probability threshold for data inclusion; data below this
            threshold will be classed as noise

        Returns
        --------
        ChildPopulationCollection
            Updated child populations with events indexing complete
        """
        sample = None
        # If parent is empty just return the child populations with empty index array
        if self.empty_parent:
            return self.child_populations
        if self.frac is not None:
            sample = self.sampling(self.data, 40000)
        # Cluster!
        model = hdbscan.HDBSCAN(core_dist_n_jobs=-1, min_cluster_size=self.min_pop_size, prediction_data=True)
        if sample is not None:
            model.fit(sample[[self.x, self.y]])
            self.data['labels'], self.data['label_strength'] = hdbscan.approximate_predict(model,
                                                                                           self.data[[self.x, self.y]])
        else:
            model.fit(self.data[[self.x, self.y]])
            self.data['labels'] = model.labels_
            self.data['label_strength'] = model.probabilities_
        # Post clustering checks
        if inclusion_threshold is not None:
            mask = self.data['label_strength'] < inclusion_threshold
            self.data.loc[mask, 'labels'] = -1
        # Predict clusters for child populations
        polygon_shapes = self.generate_polygons()
        population_predictions = self._predict_pop_clusters(polygon_shapes)
        return self._assign_clusters(population_predictions, polygon_shapes)

    def _match_pop_to_cluster(self,
                              target_population: str,
                              cluster_polygons: dict):
        """
        Internal function. Match target populations to clusters. First target population is checked to
        ensure that surrounding data-points are not all noise (neighbours checked = 1% of total events or 100 if
        > 100). If the target has not been associated to noise, then associated population to cluster whom's
        centroid is closest to the target centroid.

        Parameters
        ----------
        target_population: str
            Name of target population
        cluster_polygons: dict
            cluster polygons
        Returns
        -------
        str
            Label of cluster 'closest' to target population
        """
        target = np.array(self.child_populations.populations[target_population].properties['target'])
        target_point = Point(target[0], target[1])
        # Check which clusters a target falls into
        cluster_assingments = [cluster_label for cluster_label, poly in cluster_polygons.items()
                               if poly.contains(target_point)]
        if len(cluster_assingments) == 0:
            # Target does not fall directly into any cluster
            # Is the target surrounded by noise? (i.e. target cluster not found)
            k = int(self.data.shape[0] * 0.01)
            if k > 100:
                k = 100
            _, nearest_neighbours_idx = self.tree.query(self.data[[self.x, self.y]].values, k=k)
            neighbours = self.data.iloc[nearest_neighbours_idx[0]]
            if set(neighbours['labels'].unique()) == {-1}:
                self.warnings.append(f'Population {target_population} assigned to noise (i.e. population not found)')
                return -1
        # Not surrounded by noise, assign to nearest centroid
        cluster_centroids = [(x, centroid(self.data[self.data['labels'] == x][[self.x, self.y]].values))
                             for x in [label for label in self.data['labels'].unique() if label != -1]]
        distance_to_centroids = [(x, np.linalg.norm(target-c)) for x, c in cluster_centroids]
        return min(distance_to_centroids, key=lambda x: x[1])[0]

    def _predict_pop_clusters(self,
                              cluster_polygons: dict):
        """
        Internal method. Predict which clusters the expected child populations belong to
        using the their polygon gate or the cluster centroid

        Returns
        -------
        dict
            Predictions {child population name: cluster label}
        """
        if 'labels' not in self.data.columns:
            raise GateError('Method self.__generate_polygons called before cluster assignment')
        if set(self.data['labels'].unique()) == {-1}:
            raise GateError('Clustering algorithm failed to identify any clusters (all labels attain to noise) '
                            'If sampling, try increasing sample size')
        assignments = dict()
        for p in self.child_populations.populations.keys():
            assignments[p] = self._match_pop_to_cluster(target_population=p,
                                                        cluster_polygons=cluster_polygons)
        # Check for multiple cluster assignments
        final_assignments = dict()
        clusters_pops = collections.defaultdict(list)
        for population, cluster in assignments.items():
            clusters_pops[cluster].append(population)
        pops = set([x for sublist in clusters_pops.values() for x in sublist])
        if len(clusters_pops.keys()) != len(pops):
            self.warnings.append(f'Expected {len(pops)} populations but found {len(clusters_pops.keys())}')

        for cluster, population in clusters_pops.items():
            if len(population) > 1:
                weights = [{'name': name, 'weight': x.properties['weight']}
                           for name, x in self.child_populations.populations.items()
                           if name in population]
                priority_id = max(weights, key=lambda x: x['weight'])['name']
                self.warnings.append(f'Populations f{population} assigned to the same cluster {cluster};'
                                     f'prioritising {priority_id} based on weighting.')
                final_assignments[priority_id] = cluster
            if len(population) == 0:
                continue
            final_assignments[population[0]] = cluster
        return final_assignments

    def _assign_clusters(self,
                         target_predictions: dict,
                         polygon_shapes: dict):
        """
        Internal method. Given child population cluster predictions, update index and geom of child populations

        Parameters
        -----------
        target_predictions:
            Predicted clustering assignments {label: [population names]}

        Returns
        --------
        ChildPopulationCollection
            Child populations with updated index and geom values
        """

        for population_id, assigned_cluster in target_predictions.items():
            idx = self.data[self.data['labels'] == assigned_cluster].index.values
            self.child_populations.populations[population_id].update_index(idx=idx)
            if assigned_cluster != -1:
                x, y = polygon_shapes[assigned_cluster].exterior.xy
            else:
                x, y = list(), list()
            self.child_populations.populations[population_id].update_geom(shape='poly', x=self.x, y=self.y,
                                                                          cords=dict(x=x, y=y),
                                                                          transform_x=self.transform_x,
                                                                          transform_y=self.transform_y)
        return self.child_populations
