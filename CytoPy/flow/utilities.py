from sklearn.neighbors import BallTree, KernelDensity
from sklearn.model_selection import GridSearchCV
from scipy.stats import entropy as kl_divergence
import pandas as pd
import numpy as np
from ..data.fcs_experiments import FCSExperiment
from .gating.actions import Gating
from .transforms import scaler


def faithful_downsampling(data: np.array,
                          h: float):
    """
    An implementation of faithful downsampling as described in:  Zare H, Shooshtari P, Gupta A, Brinkman R.
    Data reduction for spectral clustering to analyze high throughput flow cytometry data. 
    BMC Bioinformatics 2010;11:403
    
    Parameters
    -----------
    data: Numpy.array
        numpy array to be down-sampled
    h: float
        radius for nearest neighbours search

    Returns
    --------
    Numpy.array
        Down-sampled array
    """
    communities = None
    registered = np.zeros(data.shape[0])
    tree = BallTree(data)
    while not all([x == 1 for x in registered]):
        i_ = np.random.choice(np.where(registered == 0)[0])
        registered[i_] = 1
        registering_idx = tree.query_radius(data[i_].reshape(1, -1), r=h)[0]
        registering_idx = [t for t in registering_idx if t != i_]
        registered[registering_idx] = 1
        if communities is None:
            communities = data[registering_idx]
        else:
            communities = np.unique(np.concatenate((communities, data[registering_idx]), 0), axis=0)
    return communities


def hellinger_dot(p: np.array,
                  q: np.array) -> np.float:
    """
    Hellinger distance between two discrete distributions.
    Original code found here: https://nbviewer.jupyter.org/gist/Teagum/460a508cda99f9874e4ff828e1896862

    Parameters
    -----------
    p: Numpy.array
        discrete probability distribution, p
    q: Numpy.array
        discrete probability distribution, q

    Returns
    --------
    Numpy.float
        Hellinger Distance
    """
    z = np.sqrt(p) - np.sqrt(q)
    return np.sqrt(z @ z / 2)


def jsd_divergence(p: np.array,
                   q: np.array) -> np.float:
    """
    Calculate the Jensen-Shannon Divergence between two PDFs

    Parameters
    -----------
    p: Numpy.array
        discrete probability distribution, p
    q: Numpy.array
        discrete probability distribution, q

    Returns
    --------
    Numpy.float
        Jenson-Shannon Divergence
    """
    m = (p + q)/2
    divergence = (kl_divergence(p, m) + kl_divergence(q, m)) / 2
    return np.sqrt(divergence)


def kde_bandwidth_cv(x,
                     bandwidth_search: tuple or None = None,
                     cv: int = 20):
    """
    Estimate best bandwidth for KDE using cross validation

    Parameters
    -----------
    x:
        data for KDE
    bandwidth_search: tuple, optional
        tuple specifying range of bandwidth values to search (start, end) in cross validation;
        if value is None, 5th and 95th quartile of data is used for lower ad upper limit respectively
    cv: int, (default=20)
        number of folds to use in cross validation (default = 20)

    Returns
    --------
    float
        Optimal bandwidth
    """
    bandwidth_search = bandwidth_search or (np.quantile(x, 0.05), np.quantile(x, 0.95))
    if bandwidth_search[0] == 0:
        bandwidth_search = (0.01, bandwidth_search[1])
    grid = GridSearchCV(KernelDensity(),
                        {'bandwidth': np.linspace(bandwidth_search[0], bandwidth_search[1], 30)},
                        cv=cv)
    grid.fit(x)
    return grid.best_estimator_.bandwidth


def kde_multivariant(x: np.array,
                     bandwidth: str or float = 'cross_val',
                     bandwidth_search: tuple or None = None,
                     bins: int or None = 1000,
                     **kwargs) -> np.array:
    """
    Perform Kernel Density Estimation for a multivariant data. Function is a wrapper for methods provided by
    the scikit-learn library. See scikit-learn documentation for available kernels (default = gaussian).
    Cross-validation available for bandwidth search by setting bandwidth argument to 'cross_val' otherwise
    a float value is expected.

    Parameters
    -----------
    x: Numpy.array
        data to perform KDE upon; if 1 dimensional data must be reshaped e.g. np.array.reshape(-1, 1)
    bandwidth: str or float, (default='cross_val')
        either float value for bandwidth or 'cross-val' to estimate bandwidth using cross-validation
    bandwidth_search: tuple, optional
        tuple specifying range of bandwidth values to search (start, end) in cross validaiton;
        ignored if bandwidth != 'cross_val'
    bins: int, (default=1000)
        bin size for generating grid of sample locations for scoring probability estimate
    kwargs:
        additional keyword arguments to pass to sklearn.neighbors.KernelDensity

    Returns
    --------
    Numpy.array
        Probability density estimate
    """
    if type(bandwidth) == str:
        assert bandwidth == 'cross_val', 'Invalid input for bandwidth, must be either float or "cross_val"'
        bandwidth = kde_bandwidth_cv(x, bandwidth_search)

    kde = KernelDensity(bandwidth=bandwidth, **kwargs)
    kde.fit(x)
    if bins is not None:
        x_grid = np.array([np.linspace(np.amin(x), np.amax(x), bins) for _ in range(x.shape[1])])
        log_pdf = kde.score_samples(x_grid.T)
    else:
        log_pdf = kde.score_samples(x)
    return np.exp(log_pdf)


def ordered_load_transform(sample_id: str,
                           experiment: FCSExperiment,
                           root_population: str,
                           transform: str,
                           scale: str or None = None,
                           sample_n: int or None = None) -> (str, pd.DataFrame or None):
    """
    Wrapper function for load_and_transform that adds convenience for multi-processing (data can be ordered post-hoc);
    returns a tuple, first element is the subject ID and the second element the population dataframe.

    Parameters
    -----------
    experiment: FCSExperiment
        Experiment object that sample belongs to
    sample_id: str
        ID for sample to load
    root_population: str
        name of root population to load from sample
    transform: str
        name of transformation method to apply (if None, data is returned untransformed)
    scale: str, optional
        name of scalling method to apply after transformation (if None, no scaling is applied)
    sample_n: int, optional
        number of events to return (sample is uniform; if None, no sampling occurs)

    Returns
    --------
    str, Pandas.DataFrame or None
        sample_id, population DataFrame
    """
    try:
        data = load_and_transform(sample_id, experiment, root_population, transform,
                                  scale, sample_n)
    except KeyError:
        print(f'Sample {sample_id} missing root population {root_population}')
        return sample_id, None
    return sample_id, data


def load_and_transform(sample_id: str,
                       experiment: FCSExperiment,
                       root_population: str,
                       transform: str or None,
                       scale: str or None = None,
                       sample_n: int or None = None) -> pd.DataFrame or None:
    """
    Standard function for loading data from an experiment, transforming, scaling, and sampling.
    experiment: Experiment object that sample belongs to

    Parameters
    -----------
    sample_id: str
        ID for sample to load
    experiment: FCSExperiment
        Experiment of interest
    root_population: str
        name of root population to load from sample
    transform: str, optional
        name of transformation method to apply (if None, data is returned untransformed)
    scale: str, optional
        name of scalling method to apply after transformation (if None, no scaling is applied)
    sample_n: int, optional
        number of events to return (sample is uniform; if None, no sampling occurs)

    Returns
    --------
    Population DataFrame or None
    """
    gating = Gating(experiment=experiment, sample_id=sample_id, include_controls=False)
    if transform is None:
        data = gating.get_population_df(root_population,
                                        transform=False,
                                        transform_features='all')
    else:
        data = gating.get_population_df(root_population,
                                        transform=True,
                                        transform_method=transform,
                                        transform_features='all')
    if scale is not None:
        data = scaler(data, scale_method=scale)[0]
    if data is None:
        raise KeyError(f'Error: unable to load data for population {root_population} for {sample_id}')
    if sample_n is not None:
        if data.shape[0] < sample_n:
            print(f'{sample_id} contains less rows than the specified sampling n {sample_n}, '
                  f'returning unsampled dataframe')
            return data
        return data.sample(sample_n)
    return data


