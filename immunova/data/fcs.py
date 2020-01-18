from immunova.data.panel import ChannelMap
from immunova.data.gating import Gate
from bson.binary import Binary
import numpy as np
import mongoengine
import pickle


class ClusteringDefinition(mongoengine.Document):
    """
    Defines the methodology and parameters of clustering to apply to an FCS File Group, or in the case of
    meta-clustering, a collection of FCS File Groups from the same FCS Experiment

    Attributes
        clustering_uid - unique identifier
        method - type of clustering performed, either PhenoGraph or FlowSOM
        parameters - parameters passed to clustering algorithm
        features - list of channels/markers that clustering is performed on
        transform_method - type of transformation to be applied to data prior to clustering
        root_population - population that clustering is performed on (default = 'root')
        cluster_prefix - a prefix to add to the name of each resulting cluster
        meta_clustering - refers to whether the clustering is 'meta-clustering'
    """
    clustering_uid = mongoengine.StringField(required=True, unique=True)
    method = mongoengine.StringField(required=True, choices=['PhenoGraph', 'FlowSOM', 'ConsensusClustering'])
    parameters = mongoengine.ListField(required=True)
    features = mongoengine.ListField(required=True)
    transform_method = mongoengine.StringField(required=False, default='logicle')
    root_population = mongoengine.StringField(required=True, default='root')
    cluster_prefix = mongoengine.StringField(required=True, default='cluster')
    meta_method = mongoengine.BooleanField(required=True, default=False)
    meta_clustering_uid_target = mongoengine.StringField(required=False)

    meta = {
        'db_alias': 'core',
        'collection': 'cluster_definitions'
    }


class Cluster(mongoengine.EmbeddedDocument):
    """
    Embedded document -> Population
    Represents a single cluster generated by a clustering experiment on a single file

    Attributes:
        cluster_id - name associated to cluster
        index - index of cell events associated to cluster
    Methods:
        save_index - save the index of data that corresponds to cells belonging to this cluster
        load_index - load the index of data that corresponds to cells belonging to this cluster
    """
    cluster_id = mongoengine.StringField(required=True)
    index = mongoengine.FileField(db_alias='core', collection_name='cluster_indexes')
    n_events = mongoengine.IntField(required=True)
    prop_of_root = mongoengine.FloatField(required=True)
    cluster_experiment = mongoengine.ReferenceField(ClusteringDefinition)
    meta_cluster_id = mongoengine.StringField(required=False)

    def save_index(self, data: np.array) -> None:
        if self.index:
            self.index.replace(Binary(pickle.dumps(data, protocol=2)))
        else:
            self.index.new_file()
            self.index.write(Binary(pickle.dumps(data, protocol=2)))
            self.index.close()

    def load_index(self) -> np.array:
        return pickle.loads(bytes(self.index.read()))


class Population(mongoengine.EmbeddedDocument):
    """
    Embedded document -> FileGroup
    Cached populations; stores the index of events associated to a population
    for quick loading.

    Attributes:
        population_name - name of population
        index - numpy array storing index of events that belong to population
        prop_of_parent - proportion of cells as a percentage of parent population
        prop_of_total - proportion of cells as a percentage of all events
        warnings - list of warnings associated to population
        parent - name of parent population
        children - list of child populations (list of strings)
        geom - list of key value pairs (tuples; (key, value)) for defining geom of population e.g.
        the definition for an ellipse that 'captures' the population
    Methods:
        save_index - given a new numpy array of index values, serialise and commit data to database
        load_index - retrieve the index values for the given population
        to_python - generate a python dictionary object for this population
        list_clustering_experiments - retrieves a list of all clustering experiments associated to this population
        pull_clusters - given a clustering experiment ID, returns a list of Cluster objects
        delete_clusters - given a clustering experiment ID, delete experiment and all associated clusters
    """
    population_name = mongoengine.StringField()
    index = mongoengine.FileField(db_alias='core', collection_name='population_indexes')
    parent = mongoengine.StringField()
    prop_of_parent = mongoengine.FloatField()
    prop_of_total = mongoengine.FloatField()
    warnings = mongoengine.ListField()
    geom = mongoengine.ListField()
    clustering = mongoengine.EmbeddedDocumentListField(Cluster)
    clusters = mongoengine.ListField()

    def save_index(self, data: np.array) -> None:
        if self.index:
            self.index.replace(Binary(pickle.dumps(data, protocol=2)))
        else:
            self.index.new_file()
            self.index.write(Binary(pickle.dumps(data, protocol=2)))
            self.index.close()

    def load_index(self) -> np.array:
        return pickle.loads(bytes(self.index.read()))

    def to_python(self) -> dict:
        geom = {k: v for k, v in self.geom}
        population = dict(name=self.population_name, prop_of_parent=self.prop_of_parent,
                          prop_of_total=self.prop_of_total, warnings=self.warnings, geom=geom,
                          parent=self.parent, index=self.load_index())
        return population

    def list_clustering_experiments(self):
        return [c.cluster_experiment.clustering_uid for c in self.clustering]

    def pull_clusters(self, clustering_uid: str):
        if clustering_uid not in self.list_clustering_experiments():
            raise ValueError(f'Error: a clustering experiment with UID {clustering_uid} does not exist')
        return [c for c in self.clustering if c.cluster_experiment.clustering_uid == clustering_uid]

    def delete_clusters(self, clustering_uid: str):
        if clustering_uid not in self.list_clustering_experiments():
            raise ValueError(f'Error: a clustering experiment with UID {clustering_uid} does not exist')
        self.clustering = [c for c in self.clustering if c.cluster_experiment.clustering_uid != clustering_uid]

    def replace_cluster_experiment(self, current_uid: str, new_cluster_definition: ClusteringDefinition):
        for c in self.clustering:
            if c.cluster_experiment.clustering_uid == current_uid:
                c.cluster_experiment = new_cluster_definition

    def update_cluster(self, cluster_id: str, new_cluster: Cluster):
        self.clustering = [c for c in self.clustering if c.cluster_id != cluster_id]
        self.clustering.append(new_cluster)


class Normalisation(mongoengine.EmbeddedDocument):
    """
    Stores a normalised copy of single cell data
    Attributes:
         data - tabular normalised single cell data
         root_population - name of the root population data is derived from
         method - name of normalisation method used
    Methods:
        pull - load data and return as a multi-dimensional numpy array
        put - given a numpy array, save the data to the underlying database a new normalised data matrix
    """
    data = mongoengine.FileField(db_alias='core', collection_name='fcs_file_norm')
    root_population = mongoengine.StringField()
    method = mongoengine.StringField()

    def pull(self, sample: int or None = None) -> np.array:
        """
        Load normalised data
        :param sample: int value; produces a sample of given value
        :return:  Numpy array of events data (normalised)
        """
        data = pickle.loads(self.data.read())
        if sample and sample < data.shape[0]:
            idx = np.random.randint(0, data.shape[0], size=sample)
            return data[idx, :]
        return data

    def put(self, data: np.array, root_population: str, method: str) -> None:
        """
        Save events data to database
        :param data: numpy array of events data
        :param root_population: name of the population data is derived from
        :param method: method used for normalisation process
        :return: None
        """
        if self.data:
            self.data.replace(Binary(pickle.dumps(data, protocol=2)))
        else:
            self.data.new_file()
            self.data.write(Binary(pickle.dumps(data, protocol=2)))
            self.data.close()
        self.root_population = root_population
        self.method = method


class File(mongoengine.EmbeddedDocument):
    """
    Embedded document -> FileGroup
    Document representation of a single FCS file.

    Attributes:
        file_id - unique identifier for fcs file
        file_type - one of either 'complete' or 'control'; signifies the type of data stored
        data - numpy array of fcs events data
        norm - numpy array of normalised fcs events data
        compensated - boolean value, if True then data have been compensated
        channel_mappings - list of standarised channel/marker mappings (corresponds to column names of underlying data)

    Methods:
        pull - loads data, returning a multi-dimensional numpy array
        put - given a numpy array, data is serialised and stored
    """
    file_id = mongoengine.StringField(required=True)
    file_type = mongoengine.StringField(default='complete')
    data = mongoengine.FileField(db_alias='core', collection_name='fcs_file_data')
    norm = mongoengine.EmbeddedDocumentField(Normalisation)
    compensated = mongoengine.BooleanField(default=False)
    channel_mappings = mongoengine.EmbeddedDocumentListField(ChannelMap)

    def pull(self, sample: int or None = None) -> np.array:
        """
        Load raw data
        :param sample: int value; produces a sample of given value
        :return:  Numpy array of events data (raw)
        """
        data = pickle.loads(self.data.read())
        if sample and sample < data.shape[0]:
            idx = np.random.randint(0, data.shape[0], size=sample)
            return data[idx, :]
        return data

    def put(self, data: np.array) -> None:
        """
        Save events data to database
        :param data: numpy array of events data
        :param typ: type of data; either `data` (raw) or `norm` (normalised)
        :return: None
        """
        if self.data:
            self.data.replace(Binary(pickle.dumps(data, protocol=2)))
        else:
            self.data.new_file()
            self.data.write(Binary(pickle.dumps(data, protocol=2)))
            self.data.close()


class FileGroup(mongoengine.Document):
    """
    Document representation of a file group; a selection of related fcs files (e.g. a sample and it's associated
    controls)

    Attributes:
        primary_id - unique ID to associate to group
        files - list of File objects
        flags - warnings associated to file group
        notes - additional free text
        populations - populations derived from this file group
        gates - gate objects that have been applied to this file group
    Methods:
        delete_populations - delete populations
        delete_gates - delete gates
        validity - search flags for the 'invalid', returns False if found
    """
    primary_id = mongoengine.StringField(required=True)
    files = mongoengine.EmbeddedDocumentListField(File)
    flags = mongoengine.StringField(required=False)
    notes = mongoengine.StringField(required=False),
    collection_datetime = mongoengine.DateTimeField(required=False)
    processing_datetime = mongoengine.DateTimeField(required=False)
    populations = mongoengine.EmbeddedDocumentListField(Population)
    gates = mongoengine.EmbeddedDocumentListField(Gate)
    meta = {
        'db_alias': 'core',
        'collection': 'fcs_files'
    }

    def delete_populations(self, populations: list or str) -> None:
        """
        Delete one or more populations from FileGroup
        :param populations: either a list of population names or 'all' to delete all associated populations
        :return: None
        """
        if populations == all:
            self.populations = []
        else:
            self.populations = [p for p in self.populations if p.population_name not in populations]
        self.save()

    def update_population(self, population_name: str, new_population: Population):
        self.populations = [p for p in self.populations if p.population_name != population_name]
        self.populations.append(new_population)
        self.save()

    def delete_gates(self, gates: list or str):
        """
        Delete one or many gates from FileGroup
        :param gates: either a list of gate names or 'all' to delete all associated gates
        :return: None
        """
        if gates == all:
            self.gates = []
        else:
            self.gates = [g for g in self.gates if g.gate_name not in gates]
        self.save()

    def validity(self) -> bool:
        """
        If 'invalid' found in Flags, will return False
        :return: True if valid (invalid in flags), else False
        """
        if self.flags is None:
            return True
        if 'invalid' in self.flags:
            return False
        return True

