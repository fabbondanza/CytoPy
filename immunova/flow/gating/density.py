from immunova.flow.gating.utilities import kde, check_peak, find_local_minima
from immunova.flow.gating.defaults import ChildPopulationCollection
from immunova.flow.gating.base import Gate, GateError
from scipy.signal import find_peaks
import pandas as pd
import numpy as np


class DensityThreshold(Gate):
    def __init__(self, data: pd.DataFrame, x: str, child_populations: ChildPopulationCollection, y: str or None = None,
                 kde_bw: float = 0.01, ignore_double_pos: bool = False, std: float or None = None,
                 q: float or None = 0.95, peak_threshold: float or None = None, frac: float or None = 0.5,
                 downsample_method: str = 'uniform', density_downsample_kwargs: dict or None = None):
        """
        Threshold gating estimated using properties of the KDE of events data
        :param data: pandas dataframe containing compensated and transformed flow cytometry data
        :param x: name of column to gate
        :param child_populations: child populations expected as output (ChildPopulationCollection; see docs for info)
        :param q: if only 1 peak is found, quantile gating is performed using this argument as the quantile
        :param std: alternative to quantile gating, the number of standard deviations from the mean can be used to
        determine the threshold
        :param kde_bw: bandwidth for gaussian kernel density smoothing
        :param frac: estimating the kernel density can be computationally expensive. By default this
        is estimated using a sample of the data. This parameter defines the fraction of data to use for kde estimation
        :param peak_threshold: if not None, then this value should be a float. This decimal value represents what the
        minimum height of a peak should be relevant to the highest peak found (e.g. if peak_threshold=0.05, then all peaks
        with a height < 0.05 of the heighest peak will be ignored)
        :param ignore_double_pos: if True, in the case that multiple peaks are detected, peaks to the right of
        the highest peak will be ignored in the local minima calculation
        :param downsample_method: methodology to use for down-sampling prior to clustering (either 'uniform' or
        'density')
        :param density_downsample_kwargs: arguments to pass to density dependent down-sampling function (if method
        is 'uniform' leave value as None)
        """
        super().__init__(data=data, x=x, y=y, child_populations=child_populations, frac=frac,
                         downsample_method=downsample_method, density_downsample_kwargs=density_downsample_kwargs)
        self.kde_bw = kde_bw
        self.ignore_double_pos = ignore_double_pos
        self.std = std
        self.q = q
        self.peak_threshold = peak_threshold
        self.sample = self.sampling(self.data, 5000)

    def __find_peaks(self, probs: np.array) -> np.array:
        """
        Internal method. Perform peak finding (see scipy.signal.find_peaks for details)
        :param probs: array of probability estimates generated using flow.gating.utilities.kde
        :return: array of indices specifying location of peaks in `probs`
        """
        # Find peaks
        peaks = find_peaks(probs)[0]
        if self.peak_threshold:
            peaks = check_peak(peaks, probs, self.peak_threshold)
        return peaks

    def __evaluate_peaks(self, data: pd.DataFrame, peaks: np.array, probs: np.array, xx: np.array) -> float and str:
        """
        Internal method. Given the outputs of `__find_peaks` and `__smooth` calculate the threshold to generate
        for gating. If a single peak (one population) is found use quantile or standard deviation. If multiple peaks
        are found (multiple populations) then look for region of minimum density.
        :param peaks: array of indices specifying location of peaks in `probs`
        :param probs: array of probability estimates generated using flow.gating.utilities.kde
        :param xx: array of linear space kde calculated across
        :return: threshold, method used to generate threshold
        """
        method = ''
        threshold = None
        # Evaluate peaks
        if len(peaks) == 1:
            # 1 peak found, use quantile or standard deviation to find threshold
            if self.q:
                threshold = data[self.x].quantile(self.q, interpolation='nearest')
                method = 'Quantile'
            elif self.std:
                u = data[self.x].mean()
                s = data[self.x].std()
                threshold = u + (s * self.std)
                method = 'Standard deviation'
            else:
                # No std or q provided so using default of 95th quantile
                threshold = data[self.x].quantile(0.95, interpolation='nearest')
                method = 'Quantile'
        if len(peaks) > 1:
            # Multiple peaks found, find the local minima between pair of highest peaks
            if self.ignore_double_pos:
                # Merge peaks of 'double positive' populations
                probs_peaks = probs[peaks]
                highest_peak = np.where(probs_peaks == max(probs_peaks))[0][0]
                if highest_peak < len(peaks):
                    peaks = peaks[:highest_peak + 1]
            threshold = find_local_minima(probs, xx, peaks)
            method = 'Local minima between pair of highest peaks'
        return threshold, method

    def __calc_threshold(self, data: pd.DataFrame, x: str) -> float and str:
        """
        Internal method. Wrapper for calculating threshold for gating.
        :param x: feature of interest for threshold calculation
        :return: threshold, method used to generate threshold
        """
        probs, xx = kde(data, x, self.kde_bw)
        peaks = self.__find_peaks(probs)
        return self.__evaluate_peaks(data, peaks, probs, xx)

    def __child_update_1d(self, threshold: float, method: str, merge_options: str) -> None:
        """
        Internal method. Given a threshold and method generated from 1 dimensional threshold gating, update the objects child
        population collection.
        :param threshold: threshold value for gate
        :param method: method used for generating threshold
        :param merge_options: must have value of 'overwrite' or 'merge'. Overwrite: existing index values in child
        populations will be overwritten by the results of the gating algorithm. Merge: index values generated from
        the gating algorithm will be merged with index values currently associated to child populations
        :return: None
        """
        neg = self.child_populations.fetch_by_definition('-')
        pos = self.child_populations.fetch_by_definition('+')
        if neg is None or pos is None:
            GateError('Invalid ChildPopulationCollection; must contain definitions for - and + populations')
        pos_pop = self.data[self.data[self.x] > threshold]
        neg_pop = self.data[self.data[self.x] < threshold]
        for x in [pos, neg]:
            self.child_populations.populations[x].update_geom(shape='threshold_1d', x=self.x, y=self.y,
                                                              method=method)
        self.child_populations.populations[pos].update_index(idx=pos_pop.index.values, merge_options=merge_options)
        self.child_populations.populations[neg].update_index(idx=neg_pop.index.values, merge_options=merge_options)

    def gate_1d(self, merge_options: str = 'overwrite') -> ChildPopulationCollection:
        """
        Perform density based threshold gating in 1 dimensional space
        :param merge_options: must have value of 'overwrite' or 'merge'. Overwrite: existing index values in child
        populations will be overwritten by the results of the gating algorithm. Merge: index values generated from
        the gating algorithm will be merged with index values currently associated to child populations
        :return: Updated child population collection
        """
        # If parent is empty just return the child populations with empty index array
        if self.empty_parent:
            return self.child_populations
        if self.sample is not None:
            threshold, method = self.__calc_threshold(self.sample, self.x)
        else:
            threshold, method = self.__calc_threshold(self.data, self.x)
        if not threshold:
            raise GateError('Unexpected error whilst performing threshold gating. Calculated threshold is Null.')
        # Update child populations
        self.__child_update_1d(threshold, method, merge_options)
        return self.child_populations

    def __child_update_2d(self, x_threshold: float, y_threshold: float, method: str) -> None:
        """
        Internal method. Given thresholds and method generated from 2 dimensional threshold gating,
        update the objects child population collection.
        :param x_threshold: threshold value for gate in x-dimension
        :param y_threshold: threshold value for gate in y-dimension
        :param method: method used for generating threshold
        :return: None
        """
        xp_idx = self.data[self.data[self.x] > x_threshold].index.values
        yp_idx = self.data[self.data[self.y] > y_threshold].index.values
        xn_idx = self.data[self.data[self.x] < x_threshold].index.values
        yn_idx = self.data[self.data[self.y] < y_threshold].index.values

        negneg = self.child_populations.fetch_by_definition('--')
        pospos = self.child_populations.fetch_by_definition('++')
        posneg = self.child_populations.fetch_by_definition('+-')
        negpos = self.child_populations.fetch_by_definition('-+')

        if any([x is None for x in [negneg, negpos, posneg, pospos]]):
            GateError('Invalid ChildPopulationCollection; must contain definitions for --, -+, +-, and ++ populations')

        pos_idx = np.intersect1d(xn_idx, yn_idx)
        self.child_populations.populations[negneg].update_index(idx=pos_idx, merge_options='merge')
        pos_idx = np.intersect1d(xp_idx, yp_idx)
        self.child_populations.populations[pospos].update_index(idx=pos_idx, merge_options='merge')
        pos_idx = np.intersect1d(xn_idx, yp_idx)
        self.child_populations.populations[negpos].update_index(idx=pos_idx, merge_options='merge')
        pos_idx = np.intersect1d(xp_idx, yn_idx)
        self.child_populations.populations[posneg].update_index(idx=pos_idx, merge_options='merge')

        for x in [negneg, negpos, posneg, pospos]:
            self.child_populations.populations[x].update_geom(shape='threshold_2d', x=self.x,
                                                              y=self.y, method=method)

    def gate_2d(self) -> ChildPopulationCollection:
        """
        Perform density based threshold gating in 2 dimensional space
        :return: Updated child population collection
        """
        # If parent is empty just return the child populations with empty index array
        if self.empty_parent:
            return self.child_populations
        if not self.y:
            raise GateError('For a 2D threshold gate a value for `y` is required')
        if self.sample is not None:
            x_threshold, x_method = self.__calc_threshold(self.sample, self.x)
            y_threshold, y_method = self.__calc_threshold(self.sample, self.y)
        else:
            x_threshold, x_method = self.__calc_threshold(self.data, self.x)
            y_threshold, y_method = self.__calc_threshold(self.data, self.y)
        if not x_threshold or not y_threshold:
            raise GateError('Unexpected error whilst performing threshold gating. Calculated threshold is Null.')
        method = f'X: {x_method}, Y: {y_method}'
        self.__child_update_2d(x_threshold, y_threshold, method)
        return self.child_populations
