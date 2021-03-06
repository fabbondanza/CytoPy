********************************************************************
Single-cell phenotype classification by high-dimensional clustering
********************************************************************

CytoPy supports the application of a clustering algorithm to any population, seperating cells into clusters of similar phenotype in high dimensional space. Currently CytoPy supports PhenoGraph clustering and FlowSOM. Clustering is implemented using the **Clustering** class and clusters are saved to the **Population** of each biological sample. Multiple instances of a clustering algorithm can be applied to the same population and the design of the MongoDB database makes it easy to contrast and compare populations (produced from both autonomous/manual gates and from supervised classifiers) with the results of clustering algorithms.

The ClusteringDefinition
=========================

We mentioned that multiple clustering algorithms can be applied to a **Population** and their results saved to the database. This is made possible because of the **ClusteringDefinition** class. Before we apply any clustering algorithm to a **Population** we first create a **ClusteringDefinition**. This can be thought of as a "clustering experiment design". It contains all the information necessary to replicate a clustering analysis: the algorithm to use, the hyperparameters, and the population the algorithm is applied to. The **ClusteringDefinition** is saved to the underlying database and can be reloaded and reused. A reference to the **ClusteringDefinition** is saved to each cluster produced as a result of that defintion. Therefore, when we want to reload clusters from a **Population** to visualise or interpret, we simply provide the ID for the **ClusteringDefinition** and all associated clusters are returned.

We create a **ClusteringDefinition** like so::

	from CytoPy.flow.clustering.main import ClusteringDefinition
	cd = ClusteringDefinition(clustering_uid='Tcell_clustering',
		                  features=features,
		                  method='PhenoGraph',
				  transform_method='logicle',
		                  parameters=[('k', 15)],
		                  root_population='T cells')
	cd.save()

We provide the definition with a unique identifier, this is who we will reload the definition later and also the key associated to produced clusters saved within a **Population**. We provide a list of features, these are variables measured in each fcs file that will be used as features in the clustering algorithm e.g ['CD3', 'CD4', 'CD8', CD161'....]. We specify the method i.e. the algorithm we wish to use; currently 'PhenoGraph' and 'FlowSOM' are supported but future releases could include new algorithms. The 'parameters' argument contains a list of tuples where for each tuple the first element is the name of a valid hyperparameter for the chosen algorithm and the second value the value to use for that hyperparameter. Finally, we provide the cell population we want to perform clustering on whenever this definition is used.

The **ClusteringDefinition** above would be applied to single samples and would execture PhenoGraph clustering with the defined hyperparameters producing clusters from the T cell population. Later on we will see 'meta-clustering', where a concensus of the clustering results from many biological samples is made by 'clustering the clusters'. Meta-clustering also requires a **ClusteringDefinition**. For meta-clustering we would define like above, but include additional parameters:

* meta_method: a boolean argument that defaults to False, if True, the definiton is treated as though it is for meta-clustering
* meta_clustering_uid_target: the name of the **ClusteringDefinition** for which clusters should be obtained and a consensus found

An example of creating a meta-clustering **ClusteringDefinition** is given below::

	# Load the clustering definition from above
	target_cd = ClusteringDefinition.objects(clustering_uid='Tcell_clustering').get()
	cd = ClusteringDefinition(clustering_uid='Meta_T',
		                  features=target_cd.features,
		                  method='PhenoGraph',
		                  parameters=[('k', 15)],
		                  root_population='T cells',
		                  meta_method=True,
		                  meta_clustering_uid_target=target_cd.clustering_uid)
	cd.save()

SingleClustering
=================

When we want to perform clustering analysis for a single sample, we use the **SingleClustering** class. This, like all clustering classes, inherits from the base class **Clustering**. Performing clustering is very simple. Once we have a **ClusteringDefinition** we initiate our clustering object::

	from CytoPy.flow.clustering.main import SingleClustering
	cd = ClusteringDefinition.objects(clustering_uid='Tcell_clustering').get()	
	scluster = SingleClustering(clustering_definition=cd)

We then populate this object with a biological sample from some experiment of interest::

	from CytoPy.data.project import Project
	pd_project = Project.objects(project_id='Peritonitis').get()
	experiment = pd_project.load_experiment('PD_T_PDMCs')
	# Just load the first sample from out experiment
	scluster.load_data(experiment=experiment, sample_id=experiment.list_samples()[0])

The data from this sample is stored within a Pandas DataFrame under the attribute *scluster.data*. We simply call the *cluster* method to perform clustering as defined in the **ClusteringDefinition**::

	scluster.cluster()

The cluster results are stored within a dictionary object in *scluster.clusters*, where the key corresponds to the cluster ID and the value the index of cells that belong to the cluster. When we save the cluster results to the database like so::

	scluster.save_clusters()

The clusters are saved to the **Population** called 'T cells' as specified in the **ClusteringDefinition**.

If we want to jump in and explore our clustering results however, we can do so using the **Explorer** class...

Introducing exploratory data analysis with Explorer
====================================================

The **Explorer** class is the ultimate tool of explroatory data analysis in CytoPy. Every clustering class in CytoPy has a method called *explorer* that generates an **Explorer** object. The **Explorer** can be thought of as a wrapper to a Pandas DataFrame that brings immense data wrangling and visualisation power. **Explorer** houses either single cell data from a single biological sample or the results of meta-clustering in it's *data* attribute. It then contains many methods for visualising this data and exploring it interactively, as well as relating this data to patient metadata.

When the **Explorer** object is generated the data is populated with labels of the clustering results, **Population** labels for each single cell, and identifiers that relate each single cell back to the biological subject it originated from. Let's see an example of **Explorer** in action::

	# Generate the Explorer object
	explorer = scluster.explorer()

We can generate a dimensionality reduction plot using any of the methods in CytoPy.flow.dim_reduction (Linear PCA, non-linear PCA, UMAP, t-SNE, Isomap, and PHATE). We can specify to plot two components as a static two dimensional scatter plot or three components that will render automatically as a three-dimensional interactive plot::

	explorer.scatter_plot(label='cluster_id', 
		              features=['CXCR3', 'CD161', 
		                        'CCR7', 'Va7-2', 
		                        'CD8', 'Vd2', 'CD45RA', 
		                        'PanGD', 'CD4','CD27'], 
		              discrete=True, 
		              n_components=2, 
		              dim_reduction_method='PHATE',
		              matplotlib_kwargs={'s': 10, 'linewidth':0.2, 'edgecolor':'black'})

.. image:: images/cluster/phate_single.png

The results of dimensionality reduction are housed within the Pandas DataFrame as additional columns. The Pandas DataFrame can be saved to hard disk using the *save* method of **Explorer** and then an **Explorer** object created from loading that DataFrame::

	explorer.save('to_use_later.csv')
	explorer = Explorer(data='to_use_later.csv')

If we want to contrast the results of our clustering analysis with the results of a supervised classifier like XGBoost, we simply change the variable we want to label data points with::

	explorer.scatter_plot(label='population_label', 
		              features=['CXCR3', 'CD161', 
		                        'CCR7', 'Va7-2', 
		                        'CD8', 'Vd2', 'CD45RA', 
		                        'PanGD', 'CD4','CD27'], 
		              discrete=True, 
		              n_components=2, 
		              dim_reduction_method='PHATE',
		              matplotlib_kwargs={'s': 10, 'linewidth':0.2, 'edgecolor':'black'})

.. image:: images/cluster/phate_xgboost.png

The performance is greatly increased because dimensionality reduction is only ever performed once and then the results stored and reused whenever the label is changed.

We can observe the phenotype of each cluster by using the *heatmap* method::

	explorer.heatmap(heatmap_var='cluster_id', 
		         features=['CXCR3', 'CD161', 
		                   'CCR7', 'Va7-2',
		                   'CD8', 'Vd2', 'CD45RA', 
		                   'PanGD', 'CD4','CD27'],
		        clustermap=True)

.. image:: images/cluster/single_heatmap.png

MetaClustering
===============

Once we have the clustering results for each biological sample in an experiment, we want to be able to group similar clusters between samples and observe them in the same space. Our manuscript (LINK) describes the methodology applied here in detail but in brief, the centroid for each cluster from each biological sample is calculated and the centroids are then clustered. This operation is handled by **MetaClustering**. The steps in code are very similar to **SingleClustering** except now we have to specify which samples we want to load::

	cd = ClusteringDefinition.objects(clustering_uid='Meta_T').get()
	meta = MetaClustering(experiment=experiment, 
			      samples=samples, 
			      clustering_definition=cd)
	meta.cluster()

The results are stored as a Pandas DataFrame, just like before. We can also create an **Explorer** object and explore the results of our meta clustering. Let's produce a heatmap of the phenotype of our meta-clusters. Remember, these clusters now represent the consensus of all our biological samples::

	explore = meta.explorer()
	features = [f for f in cd.features if f not in ['FSC-A', 'SSC-A']]
	explore.heatmap(heatmap_var='meta_cluster_id',
                normalise=False,
                clustermap=True,
                col_cluster=True,
                features=features,
                figsize=(12,8))

.. image:: images/cluster/single_heatmap.png

It would be great if we could provide our clusters with more familar names. We can do this using the *label_cluster* method of our **MetaClustering** class::

	meta.label_cluster('cluster_4', 'MAITs')
	meta.label_cluster('cluster_9', 'γδ T cells')

This can be done for each of our meta clusters. We can save the results of meta clusters to our database. Each cluster, for each biological sample, will have a field called "meta_cluster_id" that refers to it's associated meta cluster. Clusters can even be associated to multiple meta clusters at once, as this meta cluster ID is linked to the **ClusteringDefinition** that produced the meta cluster::
	
	meta.save()

If meta clustering results already exist for an experiment, we can load the existing meta clustering results::

	meta = MetaClustering(experiment=experiment, 
			      samples=samples,
			      clustering_definition=cd, 
			      load_existing_meta=True)

Let's use the **Explorer** class to explore the newly labelled meta clusters::

	explore = meta.explorer()
	explore.heatmap(heatmap_var='meta_cluster_id',
		        clustermap=True,
		        col_cluster=True,
		        features=features,
		        figsize=(8,8),
		        vmin=0, 
			vmax=1)

.. image:: images/cluster/meta_heatmap_2.png

The plots of CytoPy use common libraries:
* Heatmaps are produced using Seaborn
* Scatterplots in **Explorer** are produced using Scprep
* All other plots use Matplotlib

Additional keyword arguments that are common to these libraries can be given and will be passed to the call to Seaborn/Scprep/Matplotlib.

We can visualise meta clusters as a scatter plot where all clusters from all biological samples are shown after dimensionality reduction. The colour of the data point corresponds to it's meta cluster assignement and the size of the data point the proportion of cellular events relative to the biological sample the cluster originated from. The size of data points can be controlled using the 'meta_scale_factor' argument::

	explore.scatter_plot(label='meta_cluster_id', 
		             features=cd.features, 
		             discrete=True, 
		             meta=True, 
		             meta_scale_factor=4000,
		             matplotlib_kwargs={'edgecolors': 'black',
		                                'linewidth': 1},
		             figsize=(15,10),
		             dim_reduction_method='UMAP'})

.. image:: images/cluster/meta_umap.png

The crown jewl of CytoPy is its ability to easily and rapidly relate the results of complex cytometry analysis to the underlying clinical or experimental meta data. In the **Explorer** class we can load meta data using the *load_meta* method. We provide any field name in the **Subject** document that a column is amended to the Pandas DataFrame for that variable. Additionally we can load drug data, infection data, and other embedded data where multiple events of a variable exist for one patient (see CytoPy.flow.clustering.main.Explorer). 

Below is an example of loading the peritonitis variables, which specifies if a patient has peritonitis or not. We can then colour clusters according to this variable::

	explore.load_meta('peritonitis')
	explore.scatter_plot(label='peritonitis', 
                             features=cd.features, 
                             discrete=True, 
                             meta=True, 
                             meta_scale_factor=4000,
                             matplotlib_kwargs={'edgecolors': 'black',
                                                'linewidth': 1},
                             figsize=(12,10),
                             dim_reduction_method='UMAP')

.. image:: images/cluster/meta_umap_meta.png
