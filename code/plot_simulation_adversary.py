#!/usr/bin/env python
# -*- coding: utf-8 -*-

import sys, os
import argparse
import pickle
import logging

import scipy.stats
import numpy as np
import pandas as pd
import seaborn as sns
from matplotlib import pyplot as plt


def parse_args():
    parser = argparse.ArgumentParser(description="Summarize csvs by taking mean")
    parser.add_argument("--seed", type=int, default=0, help="seed")
    parser.add_argument("--results", type=str)
    parser.add_argument("--plot-file", type=str, default="_output/plot.png")
    args = parser.parse_args()
    args.results = args.results.split(",")
    return args


def main():
    args = parse_args()

    all_res = []
    for idx, res_file in enumerate(args.results):
        if os.path.exists(res_file):
            res = pd.read_csv(res_file)
            res["seed"] = idx
            all_res.append(res)
        else:
            print("file missing", res_file)
    num_replicates = len(all_res)
    print("Number of replicates:", num_replicates)
    all_res = pd.concat(all_res)

    # Rename all the things for prettier figures
    measure_dict = {
            'curr_diff': 'Detected improvement',
            'auc': 'AUC',
            'did_approval': 'Error rate'
            }
    data_dict = {'test':'Test', 'reuse_test': 'Reusable Test'}
    mtp_dict = {
            'binary_thres': 'BinaryThres',
            'weighted_bonferroni': 'wBonferroni',
            'bonferroni': 'Bonferroni',
            'graphical_bonf_thres': 'bonfSRGP',
            'graphical_ffs': 'fsSRGP',
            'graphical_par': "presSRGP"
            }
    all_res = all_res.rename({
        "value": "Value",
        "time": "Iteration",
        }, axis=1)
    all_res["Measure"] = all_res.variable.map(measure_dict)
    all_res["Procedure"] = all_res.procedure.map(mtp_dict)
    all_res["Dataset"] = all_res.dataset.map(data_dict)
    all_res = all_res.reset_index()
    all_res.Dataset[all_res.Measure == "Error rate"] = "Reusable Test"
    max_iter = all_res.Modification.max()
    print(all_res[(all_res.variable == "did_approval") & (all_res.Modification ==
        max_iter)].groupby("Procedure").mean())

    print(all_res[all_res.Modification == max_iter])

    sns.set_context("paper", font_scale=2.5)
    rel_plt = sns.relplot(
        data=all_res[all_res.variable.isin(list(measure_dict.keys()))],
        x="Modification",
        y="Value",
        hue="Procedure",
        #row="Dataset",
        col="Measure",
        kind="line",
        style="Dataset",
        facet_kws={"sharey": False, "sharex": True},
        linewidth=3,
    )
    rel_plt.set_titles('{col_name}')
    print(rel_plt.axes_dict.keys())
    plt.savefig(args.plot_file)
    print("Fig", args.plot_file)


if __name__ == "__main__":
    main()
