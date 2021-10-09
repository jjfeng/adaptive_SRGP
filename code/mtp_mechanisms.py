import logging
from typing import List
import subprocess

import numpy as np
from scipy.stats import norm, multivariate_normal
import scipy.optimize

from constants import RSCRIPT_PATH

def get_losses(test_y, pred_y):
    test_y = test_y.flatten()
    pred_y = np.maximum(np.minimum(1 - 1e-10, pred_y.flatten()), 1e-10)
    test_nlls = -(np.log(pred_y) * test_y + np.log(1 - pred_y) * (1 - test_y))
    return test_nlls


class BinaryThresholdMTP:
    """
    Repeatedly test at level alpha
    """
    name = "binary_thres"

    def __init__(self, base_threshold, alpha):
        self.base_threshold = base_threshold
        self.alpha = alpha

    def set_num_queries(self, num_adapt_queries):
        self.num_adapt_queries = num_adapt_queries

    def get_test_compare(self, test_y, pred_y, prev_pred_y, predef_pred_y=None):
        """
        @return test perf where 1 means approve and 0 means not approved
        """
        test_nlls_new = get_losses(test_y, pred_y)
        test_nlls_prev = get_losses(test_y, prev_pred_y)
        loss_diffs = test_nlls_new - test_nlls_prev
        t_stat_se = np.sqrt(np.var(loss_diffs) / loss_diffs.size)
        upper_ci = np.mean(loss_diffs) + t_stat_se * norm.ppf(1 - self.alpha)
        print("upper ci", upper_ci, loss_diffs.mean(), "THRES")
        return int(upper_ci < 0)


class BonferroniThresholdMTP(BinaryThresholdMTP):
    name = "bonferroni_thres"

    def __init__(self, base_threshold, alpha):
        self.base_threshold = base_threshold
        self.alpha = alpha

    def set_num_queries(self, num_adapt_queries):
        self.num_adapt_queries = num_adapt_queries
        self.correction_factor = np.power(2, num_adapt_queries)
        print(num_adapt_queries, self.correction_factor)

    def get_test_compare(self, test_y, pred_y, prev_pred_y, predef_pred_y=None):
        """
        Test against a previously approved model, following multiple testing procedure

        @param test_y: observed y outcomes in test data
        @param pred_y: predicted y by modification
        @param prev_pred_y: predicted y by previous model
        @param predef_pred_y: predicted y by modification generated by prespecified updating procedure

        @return test perf where 1 means approve and 0 means not approved
        """
        test_nlls_new = get_losses(test_y, pred_y)
        test_nlls_prev = get_losses(test_y, prev_pred_y)
        loss_diffs = test_nlls_new - test_nlls_prev
        t_stat_se = np.sqrt(np.var(loss_diffs) / loss_diffs.size)
        t_thres = norm.ppf(self.alpha / self.correction_factor)
        t_statistic = np.mean(loss_diffs) / t_stat_se
        upper_ci = np.mean(loss_diffs) + t_stat_se * norm.ppf(
            1 - self.alpha / self.correction_factor
        )
        print("BONF t statistic", t_statistic, t_thres)
        print("BONF upper ci", upper_ci)
        return int(upper_ci < 0)


class Node:
    """
    Node in the graph for sequentially rejective graphical procedure (SRGP)
    """
    def __init__(self, weight, success_edge, history, parent=None):
        """
        @param subfam_root: which node is the subfamily's root node. if none, this is the root
        """
        self.success = None
        self.success_edge = success_edge
        self.failure_edge = 1 - success_edge
        self.failure = None
        self.weight = weight
        self.history = history
        self.parent = parent

    def observe_losses(self, test_losses):
        self.test_losses = test_losses

    def set_test_thres(self, thres):
        self.test_thres = thres

    def earn(self, weight_earn):
        self.weight += weight_earn
        self.local_alpha = None


class GraphicalBonfMTP(BinaryThresholdMTP):
    name = "graphical_bonf_thres"

    def __init__(
        self,
        base_threshold,
        alpha,
        success_weight,
        alpha_alloc_max_depth: int = 0,
        scratch_file: str = None,
    ):
        self.base_threshold = base_threshold
        self.alpha = alpha
        self.success_weight = success_weight
        assert alpha_alloc_max_depth == 0
        self.alpha_alloc_max_depth = alpha_alloc_max_depth
        self.parallel_ratio = 0
        self.scratch_file = scratch_file

    def _create_children(self, node, query_idx):
        children = [Node(
            weight=0,
            success_edge=self.success_weight,
            history=node.history + ([1] if query_idx >= 0 else []) + [0] * i,
            parent=node,
            ) for i in range(self.num_adapt_queries - query_idx - 1)]
        print("make num childs", len(children), query_idx)
        for c in children:
            print(c.history)
        node.children = children
        node.children_weights = [
            self.success_weight *  np.power(1 - self.success_weight, i)
            for i in range(query_idx + 1, self.num_adapt_queries)]
        if children:
            node.children_weights[-1] = 1 - np.sum(node.children_weights[:-1])

    def set_num_queries(self, num_adapt_queries):
        # reset num queries, -1 indicates start node
        self.num_queries = -1
        self.num_adapt_queries = num_adapt_queries
        self.test_hist = []

        self.start_node = Node(
            1,
            success_edge=self.success_weight,
            history=[],
            parent=None,
        )
        self._create_children(self.start_node, self.num_queries)
        self.test_tree = self.start_node

        # propagate weights from start node
        self._do_tree_update(1)

        self.parent_child_idx = 0

    def _do_tree_update(self, test_result):
        # update tree
        self.num_queries += 1

        print("NUM QUERIES", self.num_queries)
        if self.num_queries >= self.num_adapt_queries:
            # We are done
            return

        self.test_hist.append(test_result)
        if test_result == 1:
            print("DO EARN")
            for child, cweight in zip(self.test_tree.children, self.test_tree.children_weights):
                child.weight = cweight * self.test_tree.weight
            self.parent_child_idx = 0
            self.test_tree = self.test_tree.children[self.parent_child_idx]
        else:
            print("num childs", len(self.test_tree.parent.children))
            self.parent_child_idx += 1
            self.test_tree = self.test_tree.parent.children[self.parent_child_idx]
        self._create_children(self.test_tree, self.num_queries)
        self.test_tree.local_alpha = self.alpha * self.test_tree.weight

    def get_test_compare(self, test_y, pred_y, prev_pred_y, predef_pred_y=None):
        test_nlls_new = get_losses(test_y, pred_y)
        test_nlls_prev = get_losses(test_y, prev_pred_y)
        loss_diffs = test_nlls_new - test_nlls_prev
        t_stat_se = np.sqrt(np.var(loss_diffs) / loss_diffs.size)
        upper_ci = np.mean(loss_diffs) + t_stat_se * norm.ppf(
            1 - self.test_tree.local_alpha
        )
        test_result = int(upper_ci < 0)

        self._do_tree_update(test_result)
        return test_result


class GraphicalFFSMTP(GraphicalBonfMTP):
    name = "graphical_ffs"

    def _get_prior_losses(self, node):
        return [c.test_losses for c in node.parent.children[:self.parent_child_idx]]

    def _get_prior_thres(self, node):
        return [c.test_thres for c in node.parent.children[:self.parent_child_idx]]

    def _solve_t_statistic_thres(self, est_cov, prior_thres, alpha_level):
        if len(prior_thres) == 0:
            thres = scipy.stats.norm.ppf(alpha_level)
            print("THRES", thres, scipy.stats.norm.cdf(thres), alpha_level)
            return thres
        else:
            np.savetxt(self.scratch_file, est_cov, delimiter=",")
            cmd = [
                "Rscript",
                RSCRIPT_PATH,
                self.scratch_file,
                str(alpha_level),
            ] + list(map(str, prior_thres))
            print(" ".join(cmd))
            res = subprocess.run(cmd, stdout=subprocess.PIPE)
            thres = float(res.stdout.decode("utf-8")[4:])
            print("THRES FROM R", thres)
            return thres

    def _get_min_t_stat_thres_approx(self, est_cov, prior_thres, alpha_level, num_tries=0):
        """
        need to find the critical value at which alpha spending is no more than specified,
        over all null hypothesis configurations

        we can't do this since it's intractable. instead we do an approximation with
        randomly drawn hypothesis configurations and take the minimum critical value

        @param num_tries: number of random hypothesis configurations to test
        @param est_cov: the estimated covariance structure of the test statistics
        @param prior_thes: the previously selected critical values thresholds
        @param alpha_level: how much alpha to spend
        """
        # Threshold if all hypos are true
        thres = self._solve_t_statistic_thres(est_cov, prior_thres, alpha_level)

        num_hypos = len(prior_thres)
        if num_hypos <= 1 or num_tries == 0:
            return thres

        prior_thres = np.array(prior_thres)
        all_thres = [thres]
        for i in range(num_tries):
            subset_true = np.ones(num_hypos + 1, dtype=bool)
            rand_size = np.random.choice(num_hypos//2) + 1
            rand_subset_idxs = np.random.choice(num_hypos, size=rand_size)
            subset_true[rand_subset_idxs] = False
            est_cov_sub = est_cov[subset_true, :][:,subset_true]
            prior_thres_sub = prior_thres[subset_true[:-1]]
            # TODO: Technically this is all the alpha that was allocated up to the current model
            # accumulating over the last few non-null hypotheses... right now we just use alpha because
            # easy to compute. but this is technically conservative
            alpha_level_sub = alpha_level
            thres_one_false = self._solve_t_statistic_thres(est_cov_sub, prior_thres_sub, alpha_level_sub)
            all_thres.append(thres_one_false)
        print("THRES", all_thres)
        return np.min(all_thres)

    def get_test_compare(self, test_y, pred_y, prev_pred_y, predef_pred_y=None):
        loss_new = get_losses(test_y, pred_y)
        loss_prev = get_losses(test_y, prev_pred_y)
        loss_diffs = loss_new - loss_prev
        std_err = np.sqrt(np.var(loss_diffs) / loss_prev.size)
        self.test_tree.observe_losses(loss_diffs)

        prior_test_diffs = self._get_prior_losses(self.test_tree)
        prior_thres = self._get_prior_thres(self.test_tree)
        est_corr = (
            np.corrcoef(np.array(prior_test_diffs + [loss_diffs]))
            if len(prior_test_diffs)
            else np.array([[1]])
        )
        t_thres = self._get_min_t_stat_thres_approx(
            est_corr, prior_thres, self.test_tree.local_alpha
        )
        self.test_tree.set_test_thres(t_thres)

        test_stat = (np.mean(loss_diffs)) / std_err
        test_result = int(test_stat < t_thres)
        print("FFS COMPARE", test_stat, t_thres)

        # update tree
        self._do_tree_update(test_result)

        return test_result

class GraphicalParallelMTP(GraphicalFFSMTP):
    """
    Split alpha evenly across nodes generated by the "parallel" online procedure
    Model developer PRESPECIFies a parallel online procedure
    AND assumes correlation structure among models in a level
    """

    @property
    def name(self):
        return "graphical_par"

    def __init__(
        self,
        base_threshold,
        alpha,
        success_weight,
        parallel_ratio: float = 0.9,
        first_pres_weight: float = 0.5,
        alpha_alloc_max_depth: int = 0,
        scratch_file: str = None,
    ):
        self.base_threshold = base_threshold
        self.alpha = alpha
        self.success_weight = success_weight
        self.parallel_ratio = parallel_ratio
        self.first_pres_weight = first_pres_weight
        self.alpha_alloc_max_depth = alpha_alloc_max_depth
        self.scratch_file = scratch_file

    def _create_children(self, node):
        child_weight = (
            (1 - self.parallel_ratio) / np.power(2, self.alpha_alloc_max_depth)
            if self.num_queries < self.alpha_alloc_max_depth
            else 0
        )
        node.success = Node(
            child_weight,
            success_edge=self.success_weight,
            history=node.history + [1],
            subfam_root=None,
            parent=node,
        )
        node.failure = Node(
            child_weight,
            success_edge=self.success_weight,
            history=node.history + [0],
            subfam_root=node.subfam_root,
            parent=node,
        )

    def _get_prior_losses(self, node):
        if node is None:
            return []
        return self._get_prior_losses(node.parent) + [node.test_losses]

    def _get_prior_thres(self, node):
        if node is None:
            return []
        return self._get_prior_thres(node.parent) + [node.test_thres]

    def set_num_queries(self, num_adapt_queries):
        # reset num queries
        self.num_queries = 0
        self.test_hist = []
        self.parallel_test_hist = []

        self.num_adapt_queries = num_adapt_queries

        # Create parallel sequence
        self.parallel_tree = Node(
            self.first_pres_weight * self.parallel_ratio,
            success_edge=1,
            history=[],
            subfam_root=None,
        )
        self.parallel_tree.local_alpha = self.parallel_tree.weight * self.alpha
        self.last_ffs_root = self.parallel_tree
        curr_par_node = self.parallel_tree
        for i in range(1, num_adapt_queries + 1):
            weight = (
                (1 - self.first_pres_weight)/num_adapt_queries * self.parallel_ratio
                if i < num_adapt_queries
                else 0
            )
            next_par_node = Node(
                weight,
                success_edge=1,
                history=[None] * i,
                subfam_root=self.parallel_tree,
                parent=curr_par_node,
            )
            curr_par_node.failure = next_par_node
            curr_par_node.success = next_par_node
            curr_par_node = next_par_node
            curr_par_node.failure = None

        # Create adapt tree
        self.test_tree = Node(
            1 - self.parallel_ratio,
            success_edge=self.success_weight,
            history=[],
            subfam_root=None,
        )
        self.test_tree.local_alpha = self.test_tree.weight * self.alpha
        self._create_children(self.test_tree)

    def _get_test_compare_ffs(self, test_y, predef_pred_y, prev_pred_y):
        """
        NOTICE that the std err used here is not the usual one!!!

        @return test perf where 1 means approve and 0 means not approved,
        """
        loss_new = get_losses(test_y, predef_pred_y)
        loss_prev = get_losses(test_y, prev_pred_y)
        loss_diffs = loss_new - loss_prev
        std_err = np.sqrt(np.var(loss_diffs) / loss_prev.size)
        self.parallel_tree.observe_losses(loss_diffs)

        # Need to traverse subfam parent nodes to decide local level
        prior_test_diffs = self._get_prior_losses(
            self.parallel_tree.parent
        )
        prior_thres = self._get_prior_thres(self.parallel_tree.parent)
        est_corr = (
            np.corrcoef(np.array(prior_test_diffs + [loss_diffs]))
            if len(prior_test_diffs)
            else np.array([[1]])
        )
        t_thres = self._solve_t_statistic_thres(
            est_corr, prior_thres, self.parallel_tree.local_alpha
        )
        self.parallel_tree.set_test_thres(t_thres)
        t_statistic = (np.mean(loss_diffs)) / std_err
        test_result = int(t_statistic < t_thres)
        print("COMPARE t_statistics", t_statistic, t_thres)
        return test_result

    def _get_test_compare_corr(self, test_y, pred_y, prev_pred_y):
        """
        @return test perf where 1 means approve and 0 means not approved,
        """
        loss_new = get_losses(test_y, pred_y)
        loss_prev = get_losses(test_y, prev_pred_y)
        loss_diffs = loss_new - loss_prev
        std_err = np.sqrt(np.var(loss_diffs) / loss_prev.size)
        self.test_tree.observe_losses(loss_diffs)

        prior_test_diffs = self._get_prior_losses(self.parallel_tree)
        prior_thres = self._get_prior_thres(self.parallel_tree)
        est_corr = (
            np.corrcoef(np.array(prior_test_diffs + [loss_diffs]))
            if len(prior_test_diffs)
            else np.array([[1]])
        )
        t_thres = self._solve_t_statistic_thres(
            est_corr, prior_thres, self.test_tree.local_alpha
        )

        test_stat = (np.mean(loss_diffs)) / std_err
        test_result = int(test_stat < t_thres)
        print("ADAPT COMPARE", test_stat, t_thres)
        return test_result

    def _do_tree_update(self, par_tree_res, adapt_tree_res):
        # update adaptive tree
        self.num_queries += 1
        self.parallel_test_hist.append(par_tree_res)
        self.test_hist.append(adapt_tree_res)

        if adapt_tree_res == 1:
            # remove node and propagate weights
            self.test_tree.success.earn(
                self.test_tree.weight * self.test_tree.success_edge
            )
            self.test_tree = self.test_tree.success
        else:
            self.test_tree.failure.earn(
                self.test_tree.weight * self.test_tree.failure_edge
            )
            self.test_tree = self.test_tree.failure
        self.test_tree.local_alpha = self.test_tree.weight * self.alpha

        self._create_children(self.test_tree)
        # Increment the par tree node regardless of success
        self.parallel_tree = self.parallel_tree.success
        self.parallel_tree.local_alpha = self.parallel_tree.weight * self.alpha

    def get_test_compare(self, test_y, pred_y, prev_pred_y, predef_pred_y):
        parallel_test_result = self._get_test_compare_ffs(
            test_y, predef_pred_y, prev_pred_y
        )
        test_result = self._get_test_compare_corr(
            test_y, pred_y, prev_pred_y
        )
        self._do_tree_update(parallel_test_result, test_result)

        return test_result
