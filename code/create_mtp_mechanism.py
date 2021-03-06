#!/usr/bin/env python
# -*- coding: utf-8 -*-

import sys, os
import time
import argparse
import pickle
import logging
import progressbar
from typing import List, Dict

import numpy as np
import pandas as pd

from hypothesis_tester import *
from mtp_mechanisms import *


def parse_args():
    parser = argparse.ArgumentParser(description="create mtp mechanism")
    parser.add_argument("--mtp-mech", type=str, default="graphical_bonf", choices=["binary_thres_mtp", "weighted_bonferroni", "bonferroni", "graphical_bonf", "graphical_prespec", "graphical_ffs"], help="Multiple testing mechanism")
    parser.add_argument(
        "--hypo-tester", type=str, default="auc", choices=["log_lik", "auc", "calib_auc"]
    )
    parser.add_argument(
        "--prespec-ratio", type=float, default=1.0, help="parallel factor"
    )
    parser.add_argument(
        "--success-weight", type=float, default=0.8, help="recycling factor"
    )
    parser.add_argument("--alpha", type=float, default=0.1, help="ci alpha")
    parser.add_argument("--bad-attempt-thres", type=int, default=3, help="attempts before this threshold get upweighted in weighted bonferroni")
    parser.add_argument("--first-pres-weight", type=float, default=0.1, help="weight for first prespecified node versus other prespecified nodes")
    parser.add_argument("--out-file", type=str, default="_output/mtp_mech.pkl")
    args = parser.parse_args()
    return args

def get_hypo_tester(hypo_tester_str):
    if hypo_tester_str == "log_lik":
        hypo_tester = LogLikHypothesisTester()
    elif hypo_tester_str == "auc":
        hypo_tester = AUCHypothesisTester()
    elif hypo_tester_str == "calib_auc":
        hypo_tester = CalibZAUCHypothesisTester()
    else:
        raise NotImplementedError("dont know this hypothesis")
    return hypo_tester


def main():
    args = parse_args()

    hypo_tester = get_hypo_tester(args.hypo_tester)

    # Create MTP mech
    if args.mtp_mech == "binary_thres_mtp":
        mtp_mech = BinaryThresholdMTP(hypo_tester, args.alpha)
    elif args.mtp_mech == "bonferroni":
        mtp_mech = BonferroniThresholdMTP(hypo_tester, args.alpha)
    elif args.mtp_mech == "weighted_bonferroni":
        mtp_mech = WeightedBonferroniThresholdMTP(hypo_tester, args.alpha, args.bad_attempt_thres)
    elif args.mtp_mech == "graphical_bonf":
        mtp_mech = GraphicalBonfMTP(
            hypo_tester, args.alpha, success_weight=args.success_weight
        )
    elif args.mtp_mech == "graphical_prespec":
        mtp_mech = GraphicalParallelMTP(
            hypo_tester,
            args.alpha,
            success_weight=args.success_weight,
            first_pres_weight=args.first_pres_weight,
            parallel_ratio=args.prespec_ratio,
        )
    elif args.mtp_mech == "graphical_ffs":
        mtp_mech = GraphicalFFSMTP(
            hypo_tester,
            args.alpha,
            success_weight=args.success_weight,
        )

    with open(args.out_file, "wb") as f:
        pickle.dump(mtp_mech, f)


if __name__ == "__main__":
    main()
