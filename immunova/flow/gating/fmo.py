from immunova.flow.gating.defaults import ChildPopulationCollection
from immunova.flow.gating.density import DensityThreshold, GateError
from scipy.stats import norm
import pandas as pd


class FMOGate(DensityThreshold):
    def __init__(self, data: pd.DataFrame, x: str, child_populations: ChildPopulationCollection,
                 fmo_x: pd.DataFrame, fmo_y: pd.DataFrame or None = None, y: str or None = None,
                 kde_bw: float = 0.01, frac: float or None = 0.5, q: float = 0.95,
                 std: float or None = None, z_score_threshold: float = 2,
                 downsample_method: str = 'uniform', density_downsample_kwargs: dict or None = None):
        """
        FMO guided density threshold gating
        :param data: pandas dataframe of fcs data for gating
        :param x: name of X dimension
        :param y: name of Y dimension (optional)
        :param child_populations: ChildPopulationCollection (see flow.gating.defaults.ChildPopulationCollection)
        :param fmo_x: pandas dataframe of fcs data for x-dimensional FMO
        :param fmo_y: pandas dataframe of fcs data for y-dimensional FMO (optional)
        :param kde_bw: bandwidth for kde calculation
        :param frac: fraction of dataset to sample for kde calculation (optional)
        :param q: quantile to use for gating if single population found
        :param std: standard deviation to use for gating if single population found (threshold calculated as population
        mean + population standard deviation * std)
        :param z_score_threshold: when multiple populations are identified in the whole panel sample the FMO gate
        is used a guide for gating. A normal distribution is fitted to the data, with the mean set as the threshold
        calculated on the whole panel sample and an std of 1. Using this distribution a z-score is calculated for the
        FMO threshold. If the z score exceeds z_score_threshold a warning is logged and the fmo is ignored.
        :param downsample_method: method used for down-sampling data (ignored if frac is None)
        :param density_downsample_kwargs: keyword arguments passed to density_dependent_downsampling
        (see flow.gating.base.density_dependent_downsampling) ignored if downsample_method != 'density' or frac is None.
        """
        super().__init__(data=data, x=x, y=y, child_populations=child_populations, kde_bw=kde_bw,
                         frac=frac, q=q, std=std, downsample_method=downsample_method,
                         density_downsample_kwargs=density_downsample_kwargs)
        self.z_score_t = z_score_threshold
        self.fmo_x = fmo_x.copy()
        self.fmo_y = fmo_y.copy()
        self.sample = self.sampling(self.data, 5000)
        self.sample_fmo_x = self.sampling(self.fmo_x, 5000)
        self.sample_fmo_y = self.sampling(self.fmo_y, 5000)

    def fmo_1d(self, merge_options: str = 'overwrite') -> ChildPopulationCollection:
        """
        Perform FMO gating in 1 dimensional space
        :param merge_options: must have value of 'overwrite' or 'merge'. Overwrite: existing index values in child
        populations will be overwritten by the results of the gating algorithm. Merge: index values generated from
        the gating algorithm will be merged with index values currently associated to child populations
        :return: Updated child population collection
        """
        if self.empty_parent:
            return self.child_populations

        # Calculate threshold
        if self.sample is not None:
            data = self.sample
        else:
            data = self.data
        if self.sample_fmo_x is not None:
            fmo = self.sample_fmo_x
        else:
            fmo = self.fmo_x
        threshold, method = self.__1d(data, fmo, self.x)

        self.__child_update_1d(threshold, method, merge_options)
        return self.child_populations

    def __1d(self, whole: pd.DataFrame, fmo: pd.DataFrame, feature: str) -> float and str:
        """
        Internal method. Calculate FMO guided threshold gate in 1 dimensional space.
        :param whole: pandas dataframe for events data in whole panel sample
        :param fmo: pandas dataframe for events data in fmo sample
        :param feature: name of the feature to perform gating on
        :return: threshold, method used to obtain threshold
        """
        if fmo.shape[0] == 0:
            raise GateError('No events in parent population in FMO!')
            # Calculate threshold for whole panel (primary sample)
        whole_threshold, whole_method = self.__calc_threshold(whole, feature)
        fmo_threshold, fmo_method = self.__calc_threshold(fmo, feature)
        if whole_method in ['Quantile', 'Standard deviation']:
            return fmo_threshold, fmo_method
        elif whole_method == 'Local minima between pair of highest peaks':
            p = norm.cdf(x=fmo_threshold, loc=whole_threshold, scale=0.1)
            z_score = norm.ppf(p)
            if abs(z_score) >= self.z_score_t:
                self.warnings.append("""FMO threshold z-score >2 (see documentation); the threshold 
                as determined by the FMO is a significant distance from the region of minimum density between the 
                two highest peaks see in the whole panel, therefore the FMO has been ignored. 
                Manual review of gating is advised.""")
                return whole_threshold, whole_method
            else:
                # Take an average of fmo and whole panel threshold
                threshold = (whole_threshold + fmo_threshold)/2
                return threshold, 'FMO guided minimum density threshold'
        else:
            GateError('Unrecognised method returned from __calc_threshold')

    def fmo_2d(self) -> ChildPopulationCollection:
        """
        Perform FMO gating in 2-dimensional space
        :return: Updated child populations
        """
        # If parent is empty just return the child populations with empty index array
        if self.empty_parent:
            return self.child_populations
        if not self.y:
            raise GateError('For a 2D threshold gate a value for `y` is required')

        # Calculate threshold
        if self.sample is not None:
            data = self.sample
        else:
            data = self.data

        if self.sample_fmo_x is not None:
            fmo = self.sample_fmo_x
        else:
            fmo = self.fmo_x
        x_threshold, x_method = self.__1d(data, fmo, self.x)

        if self.sample_fmo_y is not None:
            fmo = self.sample_fmo_y
        else:
            fmo = self.fmo_y
        y_threshold, y_method = self.__1d(data, fmo, self.y)
        method = f'X: {x_method}, Y: {y_method}'
        self.__child_update_2d(x_threshold, y_threshold, method)
        return self.child_populations
