import logging
from copy import deepcopy
from typing import List
import numpy as np
import pandas as pd
import scipy.optimize
import sklearn.base
from sklearn.linear_model import LogisticRegression, LogisticRegressionCV
from sklearn.ensemble import RandomForestClassifier
from sklearn.ensemble import GradientBoostingClassifier

from custom_models import *
from dataset import Dataset, DataGenerator
from hypothesis_tester import get_log_lik

class TestHistory:
    """
    Tracks the history of test results
    """
    def __init__(self, curr_mdl, res_detail):
        self.approval_times = [0]
        self.approved_mdls = [deepcopy(curr_mdl)]
        self.proposed_mdls = [deepcopy(curr_mdl)]
        self.curr_time = 0
        self.batch_numbers = [0]
        self.did_approve = [True]
        self.res_details = [res_detail]

    def update(self, test_res: int, res_detail: pd.DataFrame, proposed_mdl, batch_number: int = None):
        """
        @param test_res: 1 if we rejected the null, 0 if we failed to reject null
        @param res_detail: pd.DataFrame with one column for each performance measure that is being tracked
        @param proposed_mdl: the model that was proposed (but maybe not approved)
        """
        self.curr_time += 1
        proposed_mdl = deepcopy(proposed_mdl)
        self.did_approve.append(test_res)
        if test_res:
            self.approval_times.append(self.curr_time)
            self.approved_mdls.append(proposed_mdl)

        self.proposed_mdls.append(proposed_mdl)
        self.res_details.append(res_detail)
        self.batch_numbers.append(batch_number)

    @property
    def tot_approves(self):
        return len(self.approval_times)

    def get_perf_hist(self):
        perf_hist = pd.concat(self.res_details).reset_index()
        perf_hist["batch_number"] = np.array(self.batch_numbers)
        col_names = list(perf_hist.columns)
        value_vars = col_names[1:]
        perf_hist["time"] = np.arange(len(self.res_details))
        return pd.melt(perf_hist, id_vars=['time'], value_vars=value_vars)

def set_model(mdl, params):
    mdl.classes_ = np.array([0, 1])
    mdl.coef_ = params[1:].reshape((1, -1))
    mdl.intercept_ = np.array([params[0]])

class LockedModeler:
    """
    This modeler does not suggest any new model
    """
    def _init_modeler(self, model_type: str):
        if model_type == "Logistic":
            modeler = LogisticRegression(penalty="none", max_iter=10000)
        elif model_type == "LogisticEven":
            modeler = LogisticRegressionEven(penalty="none", max_iter=10000)
        elif model_type == "LogisticOdd":
            modeler = LogisticRegressionOdd(penalty="none", max_iter=10000)
        elif model_type == "LogisticMod":
            modeler = LogisticRegressionMod(penalty="none", max_iter=10000)
        elif model_type == "LogisticLasso":
            modeler = LogisticRegressionCV(class_weight="balanced", penalty="l1", max_iter=10000, solver="liblinear", cv=3, n_jobs=2, Cs=5, scoring="roc_auc")
        elif model_type == "LogisticRidge":
            modeler = LogisticRegressionCV(class_weight="balanced", penalty="l2", max_iter=10000, solver="liblinear", cv=3)
        elif model_type == "RandomForest":
            modeler = RandomForestClassifier(n_estimators=n_estimators, min_samples_leaf=1, n_jobs=2)
        elif model_type == "GBT":
            modeler = GradientBoostingClassifier(loss="deviance", max_depth=1, n_estimators=100)
        else:
            raise NotImplementedError("model type missing")
        return modeler

    def __init__(self, model_type:str, seed:int = 0):
        self.modeler = self._init_modeler(model_type)

    def predict_prob(self, x):
        return self.modeler.predict_proba(x)[:, 1].reshape((-1, 1))

class AdversaryLossModeler(LockedModeler):
    """
    Given binary outputs, this adaptive modeler will try to propose modifications that are deleterious
    """
    update_dirs = [1,-1]

    def __init__(
            self, hypo_tester, data_gen: DataGenerator, update_incr: float = 0.6, ni_margin: float = 0.01
    ):
        """
        @param update_incr: how much to perturb the coefficients
        """
        self.modeler = LogisticRegression(penalty="none")
        self.hypo_tester = hypo_tester
        self.data_gen = data_gen
        self.num_sparse_theta = np.max(np.where(self.data_gen.beta.flatten() != 0)[0]) + 1
        self.update_incr = update_incr
        self.ni_margin = ni_margin

    def _set_oracle_model(self, mdl):
        mdl.coef_[:] = self.data_gen.beta.flatten()
        mdl.intercept_[:] = 0

    def simulate_approval_process(self, dat, mtp_mechanism, dat_stream=None, maxfev=10, side_dat_stream=None):
        """
        @param side_dat_stream: ignores this
        """
        # Train a good initial model
        self.modeler.fit(dat.x, dat.y.flatten())
        self._set_oracle_model(self.modeler)
        orig_coefs = self.modeler.coef_[:]

        orig_mdl = sklearn.base.clone(self.modeler)
        orig_mdl.fit(dat.x, dat.y.flatten())
        self._set_oracle_model(orig_mdl)

        # Also have some predefined perturber for reference
        # just so we can use the parallel procedure
        self.predef_modeler = sklearn.base.clone(self.modeler)
        self.predef_modeler.fit(dat.x, dat.y.flatten())
        self._set_oracle_model(self.predef_modeler)

        # Now search in each direction and do a greedy search
        curr_diff = 0
        test_hist = TestHistory(self.modeler, res_detail=pd.DataFrame({
                "curr_diff": [curr_diff],
                }))
        while test_hist.curr_time < maxfev:
            # Test each coef (dont perturb intercept)
            for var_idx in range(self.num_sparse_theta + 1, self.num_sparse_theta + 1 + dat.x.shape[1]):
                # Test update for the variable
                for update_dir in self.update_dirs:
                    test_res = 1
                    scale_factor = 1
                    while test_res == 1:
                        if test_hist.curr_time >= maxfev:
                            break
                        # Generate adaptive modification
                        curr_coef = np.concatenate(
                            [self.modeler.intercept_, self.modeler.coef_.flatten()]
                        )
                        print("var idx", var_idx)
                        curr_coef[var_idx] += update_dir * self.update_incr * scale_factor
                        logging.info("CURR_COEF %s", curr_coef)
                        proposed_mdl = sklearn.base.clone(self.modeler)
                        set_model(proposed_mdl, curr_coef)

                        # Generate predefined model
                        self.predef_modeler.coef_[0, :] = orig_coefs
                        predef_coef_idx = self.num_sparse_theta + (test_hist.curr_time // len(self.update_dirs))
                        predef_update_dir = test_hist.curr_time % len(self.update_dirs)
                        self.predef_modeler.coef_[0, predef_coef_idx] += (
                            self.update_dirs[predef_update_dir] * self.update_incr
                        )

                        # Test the performance
                        null_constraints = np.array([
                                [0, curr_diff]])
                        test_res = mtp_mechanism.get_test_res(
                            null_constraints, orig_mdl, proposed_mdl, orig_predef_mdl=orig_mdl, predef_mdl=self.predef_modeler
                        )
                        print("perturb?", test_hist.curr_time, var_idx, update_dir, test_res)
                        if test_res:
                            print("TEST RES")
                            curr_diff += self.ni_margin
                            set_model(self.modeler, curr_coef)
                            logging.info("APPROVED %s", curr_coef)
                            # If we found a good direction, keep walking in that direction,
                            # be twice as aggressive
                            scale_factor *= 2

                        test_hist.update(
                            test_res=test_res,
                            res_detail=pd.DataFrame({
                                "curr_diff": [curr_diff],
                                }),
                            proposed_mdl=proposed_mdl,
                            batch_number=test_hist.curr_time,
                        )
                    if test_res == 0 and scale_factor > 1:
                        break

        return test_hist

class OnlineAdaptLossModeler(LockedModeler):
    """
    Just do online learning on a separate dataset
    """
    def __init__(self, model_type: str, prespec_model_type: str, hypo_tester, validation_frac: float = 0.2, min_valid_dat_size: int = 200, power: float = 0.5, ni_margin: float = 0.01, predef_alpha: float = 0.1, se_factor: float = 1.96):
        """
        @param model_type: the type of model that we plan to use for proposing modifications
        @param prespec_model_type: the type of model that we plan to use for prespecification (should usually be same as model_type)
        """
        self.modeler = self._init_modeler(model_type)
        self.prespec_modeler = self._init_modeler(prespec_model_type)
        self.hypo_tester = hypo_tester
        self.validation_frac = validation_frac
        self.min_valid_dat_size = min_valid_dat_size
        self.ni_margin = ni_margin
        self.power = power
        self.predef_alpha = predef_alpha
        self.se_factor = se_factor

    def _create_train_valid_dat(self, dat: Dataset):
        valid_n = max(self.min_valid_dat_size, int(dat.size * self.validation_frac))
        train_dat = dat.subset(dat.size - valid_n)
        #print("valid_n", valid_n, train_dat.size, dat.size, self.validation_frac)
        valid_dat = dat.subset(start_n=dat.size - valid_n, n=dat.size)
        return train_dat, valid_dat

    def _do_power_calc_test_bound(self, orig_mdl, new_mdl, min_diff:float, valid_dat: Dataset, alpha: float, num_test: int, num_reps: int = 100):
        """
        @param valid_dat: data for evaluating performance of model
        @param alpha: the type I error of the current test node
        """
        logging.info("predef alpha %f", alpha)
        # use valid_dat to evaluate the model first
        self.hypo_tester.set_test_dat(valid_dat)
        res_df, orig_auc, new_auc = self.hypo_tester._get_observations(orig_mdl, new_mdl)
        res_df = res_df.to_numpy().flatten()
        logging.info("validation: new old %f auc %f", orig_auc, new_auc)
        mu_sim_raw = np.mean(res_df)
        var_sim = np.var(res_df)
        mu_sim = mu_sim_raw - np.sqrt(var_sim/valid_dat.size) * self.se_factor
        logging.info("power calc: MU SIM lower %s", mu_sim_raw)

        if mu_sim < 0:
            return 0, mu_sim

        candidate_diffs = np.arange(min_diff, mu_sim, self.ni_margin)[:1]
        if candidate_diffs.size == 0:
            logging.info("abort: no candidates found %f %f", min_diff, mu_sim)
            return 0, mu_sim

        obs_sim = np.random.normal(loc=mu_sim, scale=np.sqrt(var_sim), size=(num_test, num_reps))
        res = scipy.stats.ttest_1samp(obs_sim, popmean=candidate_diffs.reshape((-1,1)))
        candidate_power = np.mean(res.statistic > scipy.stats.norm.ppf(1 - alpha), axis=1)

        if np.any(candidate_power > self.power):
            selected_idx = np.max(np.where(candidate_power > self.power)[0])
        else:
            logging.info("abort: power too low")
            selected_idx = np.argmax(candidate_power)

        selected_thres = candidate_diffs[selected_idx]
        test_power = candidate_power[selected_idx]
        return test_power, selected_thres


    def simulate_approval_process(self, dat, mtp_mechanism, dat_stream, maxfev=10, side_dat_stream = None):
        """
        @param dat_stream: a list of datasets for further training the model
        @return perf_value
        """
        train_dat, valid_dat = self._create_train_valid_dat(dat)
        self.prespec_modeler.fit(train_dat.x, train_dat.y.flatten())
        orig_mdl = self.prespec_modeler

        curr_diff= 0
        test_hist = TestHistory(orig_mdl, res_detail=pd.DataFrame({
                "curr_diff": [0],
                }))
        test_idx = 0
        adapt_read_idx = 0
        predef_test_mdls = []
        while (test_idx < maxfev) and (adapt_read_idx < len(dat_stream)):
            print("ITERATION", test_idx)

            predef_dat = Dataset.merge([dat] + dat_stream[: adapt_read_idx + 1])
            predef_train_dat, predef_valid_dat = self._create_train_valid_dat(predef_dat)
            predef_lr = sklearn.base.clone(self.prespec_modeler)
            predef_lr.fit(predef_train_dat.x, predef_train_dat.y.flatten())
            online_mdl = sklearn.base.clone(self.modeler)
            online_mdl.fit(predef_train_dat.x, predef_train_dat.y.flatten())

            # calculate the threshold that we can test at such that the power of rejecting the null given Type I error at level alpha_node
            predef_test_power, _ = self._do_power_calc_test_bound(
                    orig_mdl,
                    predef_lr,
                    min_diff=len(predef_test_mdls) * self.ni_margin/4,
                    valid_dat=predef_valid_dat,
                    num_test=mtp_mechanism.test_set_size,
                    alpha=self.predef_alpha)

            logging.info("predef batch %d power %.5f", adapt_read_idx, predef_test_power)
            if predef_test_power >= self.power:
                # Predef will not test if power is terrible
                predef_test_mdls.append(predef_lr)
                logging.info("predef TEST idx %d, adapt idx %d, batch %d", len(predef_test_mdls) - 1, test_idx, adapt_read_idx)

            # do the same for an adaptively decided min difference
            adapt_test_power, adapt_test_diff = self._do_power_calc_test_bound(
                    orig_mdl,
                    predef_lr,
                    min_diff=curr_diff + self.ni_margin,
                    valid_dat=predef_valid_dat,
                    num_test=mtp_mechanism.test_set_size,
                    alpha=self.predef_alpha)
            logging.info("adapt batch %d power %.5f", adapt_read_idx, adapt_test_power)

            #print("COEF", predef_lr.coef_[np.abs(predef_lr.coef_) > 0])
            #print("INTERCE", predef_lr.intercept_)

            adapt_read_idx += 1
            if (adapt_test_power > self.power) and (adapt_test_diff >= (curr_diff + self.ni_margin)) and not (mtp_mechanism.require_predef and len(predef_test_mdls) <= test_idx):
                logging.info("TEST idx: %d (batch_number) %d", test_idx, adapt_read_idx)
                logging.info("TEST (avg) diff %f", adapt_test_diff)

                null_constraints = np.array([
                        [0,adapt_test_diff]])
                test_res = mtp_mechanism.get_test_res(
                    null_constraints,
                    orig_mdl,
                    new_mdl=online_mdl,
                    orig_predef_mdl=orig_mdl,
                    predef_mdl=predef_test_mdls[test_idx] if mtp_mechanism.require_predef else None
                )
                if test_res:
                    curr_diff = adapt_test_diff
                test_idx += 1
                logging.info("Test res %d", test_res)
                print("TEST RES", test_res)

                test_hist.update(
                        test_res=test_res,
                        res_detail = pd.DataFrame({
                            "curr_diff": [curr_diff]}),
                        proposed_mdl=online_mdl,
                        batch_number=adapt_read_idx,
                    )
            else:
                logging.info("CONTinuing to pull data until confident in improvement %f <  %f + %f", adapt_test_diff, curr_diff, self.ni_margin)
        logging.info("adapt read idx %d", adapt_read_idx)
        print("adapt read", adapt_read_idx)
        logging.info("TEST batch numbers %s (len %d)", test_hist.batch_numbers, len(test_hist.batch_numbers))

        return test_hist


class OnlineAdaptCalibAUCModeler(OnlineAdaptLossModeler):
    """
    Just do online learning on a separate dataset
    """
    def __init__(self, model_type:str, hypo_tester, validation_frac: float = 0.2, min_valid_dat_size: int = 200, power: float = 0.5, ni_margin: float = 0.01, calib_ni_margin: float = 0.1, predef_alpha: float = 0.1, se_factor: float = 1.96):
        self.modeler = self._init_modeler(model_type)
        self.hypo_tester = hypo_tester
        self.validation_frac = validation_frac
        self.min_valid_dat_size = min_valid_dat_size
        self.ni_margin = ni_margin
        self.calib_bounds = [-calib_ni_margin, calib_ni_margin]
        self.power = power
        self.predef_alpha = predef_alpha
        self.se_factor = se_factor

    def _do_calib_power_test(self, calib_mu_lower, calib_mu_upper, calib_var, alpha, num_test, num_reps):
        # Test calib lower
        calib_obs_sim = np.random.normal(
                loc=calib_mu_lower,
                scale=np.sqrt(calib_var), size=(num_test, num_reps))
        res = scipy.stats.ttest_1samp(calib_obs_sim, popmean=self.calib_bounds[0])
        candidate_power = np.mean(res.statistic > scipy.stats.norm.ppf(1 - alpha))

        if candidate_power < self.power:
            logging.info("abort calibration lower %f (bound %f)", candidate_power, self.calib_bounds[0])
            return False

        # Test calib upper
        calib_obs_sim = np.random.normal(
                loc=calib_mu_upper,
                scale=np.sqrt(calib_var), size=(num_test, num_reps))
        res = scipy.stats.ttest_1samp(calib_obs_sim, popmean=self.calib_bounds[1])
        candidate_power = np.mean(res.statistic < scipy.stats.norm.ppf(alpha))
        if candidate_power < self.power:
            logging.info("abort calibration upper %f", candidate_power)
            return False
        return True

    def _do_power_calc_test_bound(self, orig_mdl, new_mdl, min_diff:float, valid_dat: Dataset, alpha: float, num_test: int, num_reps: int = 200):
        """
        @param valid_dat: data for evaluating performance of model
        @param alpha: the type I error of the current test node
        """
        logging.info("predef alpha %f", alpha)
        # use valid_dat to evaluate the model first
        self.hypo_tester.set_test_dat(valid_dat)
        res_df, orig_est, new_est = self.hypo_tester._get_observations(orig_mdl, new_mdl)
        res_df = res_df.to_numpy()

        # Get performance characteristics
        mu_sim_raw = np.mean(res_df, axis=0)
        calib_var = np.var(res_df[:,0])
        auc_var = np.var(res_df[:,1])

        # Run simulation with these assumed performance characteristics (using CI lower bound)
        auc_sim = mu_sim_raw[1] - np.sqrt(auc_var/valid_dat.size) * self.se_factor
        calib_lower = mu_sim_raw[0] - np.sqrt(calib_var/valid_dat.size) * self.se_factor
        calib_upper= mu_sim_raw[0] + np.sqrt(calib_var/valid_dat.size) * self.se_factor

        logging.info("validation mu: %s", mu_sim_raw)
        logging.info("power sim mu: %f %f %f", auc_sim, calib_lower, calib_upper)
        logging.info("validation var calbi %f auc %f", calib_var/valid_dat.size, auc_var/valid_dat.size)

        is_calib_good = self._do_calib_power_test(calib_lower, calib_upper, calib_var, alpha, num_test, num_reps)
        if not is_calib_good:
            logging.info("abort calib-in-the-larg")
            return 0, auc_sim

        # Test AUC
        logging.info("power calc: AUC SIM lower %s", auc_sim)

        if (auc_sim < 0) or not np.isfinite(auc_sim):
            return 0, auc_sim

        candidate_diffs = np.arange(min_diff, auc_sim, self.ni_margin)[:1]
        if candidate_diffs.size == 0:
            logging.info("abort: no candidates found %f %f", min_diff, auc_sim)
            return 0, auc_sim

        obs_sim = np.random.normal(loc=auc_sim, scale=np.sqrt(auc_var), size=(num_test, num_reps))
        res = scipy.stats.ttest_1samp(obs_sim, popmean=candidate_diffs.reshape((-1,1)))
        candidate_power = np.mean(res.statistic > scipy.stats.norm.ppf(1 - alpha), axis=1)

        if np.any(candidate_power > self.power):
            selected_idx = np.max(np.where(candidate_power > self.power)[0])
        else:
            logging.info("abort: power too low")
            selected_idx = np.argmax(candidate_power)

        selected_thres = candidate_diffs[selected_idx]
        test_power = candidate_power[selected_idx]
        return test_power, selected_thres


    def simulate_approval_process(self, dat, mtp_mechanism, dat_stream, maxfev=10, side_dat_stream = None):
        """
        @param dat_stream: a list of datasets for further training the model
        @return perf_value
        """
        train_dat, valid_dat = self._create_train_valid_dat(dat)
        self.modeler.fit(train_dat.x, train_dat.y.flatten())
        orig_mdl = self.modeler

        curr_diff= 0
        test_hist = TestHistory(orig_mdl, res_detail=pd.DataFrame({
                "curr_diff": [0],
                }))
        test_idx = 0
        adapt_read_idx = 0
        predef_test_mdls = []
        while (test_idx < maxfev) and (adapt_read_idx < len(dat_stream)):
            logging.info("ITERATION %d %d", test_idx, adapt_read_idx)

            predef_dat = Dataset.merge([dat] + dat_stream[: adapt_read_idx + 1])
            predef_train_dat, predef_valid_dat = self._create_train_valid_dat(predef_dat)
            logging.info("TRAIN SIZE %d", predef_train_dat.size)
            predef_lr = sklearn.base.clone(self.modeler)
            predef_lr.fit(predef_train_dat.x, predef_train_dat.y.flatten())

            # calculate the threshold that we can test at such that the power of rejecting the null given Type I error at level alpha_node
            predef_test_power, _ = self._do_power_calc_test_bound(
                    orig_mdl,
                    predef_lr,
                    min_diff=len(predef_test_mdls) * self.ni_margin/2,
                    valid_dat=predef_valid_dat,
                    num_test=mtp_mechanism.test_set_size,
                    alpha=self.predef_alpha)

            logging.info("predef batch %d power %.5f", adapt_read_idx, predef_test_power)
            if predef_test_power >= self.power:
                # Predef will not test if power is terrible
                predef_test_mdls.append(predef_lr)
                logging.info("predef TEST idx %d, adapt idx %d, batch %d", len(predef_test_mdls) - 1, test_idx, adapt_read_idx)

            # do the same for an adaptively decided min difference
            adapt_test_power, adapt_test_diff = self._do_power_calc_test_bound(
                    orig_mdl,
                    predef_lr,
                    min_diff=curr_diff + self.ni_margin,
                    valid_dat=predef_valid_dat,
                    num_test=mtp_mechanism.test_set_size,
                    alpha=self.predef_alpha)
            logging.info("adapt batch %d power %.5f", adapt_read_idx, adapt_test_power)

            adapt_read_idx += 1
            if (adapt_test_power > self.power) and (adapt_test_diff >= (curr_diff + self.ni_margin)) and not (mtp_mechanism.require_predef and len(predef_test_mdls) <= test_idx):
                logging.info("TEST idx: %d (batch_number) %d", test_idx, adapt_read_idx)
                logging.info("TEST (avg) diff %f", adapt_test_diff)

                null_constraints = np.array([
                        self.calib_bounds,
                        [0,adapt_test_diff],
                        ])
                test_res = mtp_mechanism.get_test_res(
                    null_constraints, orig_mdl, predef_lr,
                    orig_predef_mdl=orig_mdl,
                    predef_mdl=predef_test_mdls[test_idx] if mtp_mechanism.require_predef else None
                )
                if test_res:
                    curr_diff = adapt_test_diff
                test_idx += 1
                logging.info("Test res %d", test_res)
                print("TEST RES", test_res)

                test_hist.update(
                        test_res=test_res,
                        res_detail = pd.DataFrame({
                            "curr_diff": [curr_diff]}),
                        proposed_mdl=predef_lr,
                        batch_number=adapt_read_idx,
                    )
            else:
                logging.info("CONTinuing to pull data until confident in improvement %f <  %f + %f", adapt_test_diff, curr_diff, self.ni_margin)
        logging.info("adapt read idx %d", adapt_read_idx)
        print("adapt read", adapt_read_idx)
        logging.info("TEST batch numbers %s (len %d)", test_hist.batch_numbers, len(test_hist.batch_numbers))

        return test_hist

