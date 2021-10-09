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

from mtp_mechanisms import *


def parse_args():
    parser = argparse.ArgumentParser(description="create mtp mechanism")
    parser.add_argument("--mtp-mech", type=str, default="graphical_bonf", choices=["binary_thres_mtp", "bonferroni", "graphical_bonf", "graphical_prespec", "graphical_ffs"], help="Multiple testing mechanism")
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.5,
        help="Threshold for defining the maximum acceptable loss by a modification",
    )
    parser.add_argument(
        "--parallel-ratio", type=float, default=1.0, help="parallel factor"
    )
    parser.add_argument(
        "--success-weight", type=float, default=0.8, help="recycling factor"
    )
    parser.add_argument("--alpha", type=float, default=0.1, help="ci alpha")
    parser.add_argument("--first-pres-weight", type=float, default=0.1, help="weight for first prespecified node versus other prespecified nodes")
    parser.add_argument("--scratch-file", type=str, default="_output/scratch.txt")
    parser.add_argument("--out-file", type=str, default="_output/mtp_mech.pkl")
    args = parser.parse_args()
    return args


def main():
    args = parse_args()

    # Create MTP mech
    if args.mtp_mech == "binary_thres_mtp":
        mtp_mech = BinaryThresholdMTP(args.threshold, args.alpha)
    elif args.mtp_mech == "bonferroni":
        mtp_mech = BonferroniThresholdMTP(args.threshold, args.alpha)
    elif args.mtp_mech == "graphical_bonf":
        mtp_mech = GraphicalBonfMTP(
            args.threshold, args.alpha, success_weight=args.success_weight
        )
    elif args.mtp_mech == "graphical_prespec":
        mtp_mech = GraphicalParallelMTP(
            args.threshold,
            args.alpha,
            success_weight=args.success_weight,
            first_pres_weight=args.first_pres_weight,
            parallel_ratio=args.parallel_ratio,
            scratch_file=args.scratch_file,
        )
    elif args.mtp_mech == "graphical_ffs":
        mtp_mech = GraphicalFFSMTP(
            args.threshold,
            args.alpha,
            success_weight=args.success_weight,
            scratch_file=args.scratch_file,
        )

    with open(args.out_file, "wb") as f:
        pickle.dump(mtp_mech, f)


if __name__ == "__main__":
    main()