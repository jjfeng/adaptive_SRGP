#!/usr/bin/env python
# -*- coding: utf-8 -*-

import sys, os
import argparse
import pickle
import logging

import scipy.stats
import numpy as np
import pandas as pd

from dataset import *

MAX_VARIABLES = 3000

def parse_args():
    parser = argparse.ArgumentParser(description="Read RSNA data for simulations")
    parser.add_argument("--seed", type=int, default=1235, help="seed for determining meta-properties of the data")
    parser.add_argument("--reuse-test-n", type=int, default=300, help="how much data is in the reusable test data")
    parser.add_argument("--init-train-n", type=int, default=10, help="how much data was used to train the initial model")
    parser.add_argument("--train-batch-n", type=int, default=1, help="how much data is observed between each iteration, in the simulated data stream")
    parser.add_argument("--random-pick-n", type=int, default=1, help="how many observations to randomly pick within a subject")
    parser.add_argument("--dat-files", type=str)
    parser.add_argument("--out-file", type=str, default="_output/data.pkl")
    parser.add_argument("--log-file", type=str, default="_output/log.txt")
    args = parser.parse_args()
    args.dat_files = args.dat_files.split(",")
    return args

def _get_data(full_dat, patient_ids, selected_ids, max_random_pick=None):
    if max_random_pick is not None:
        selected_rows = []
        for p_stay_id in selected_ids:
            stay_row_idxs = np.where(patient_ids == p_stay_id)[0]
            #print("STAY ROWS", stay_row_idxs, p_stay_id)
            if max_random_pick > stay_row_idxs.size:
                selected_idxs = stay_row_idxs
            else:
                selected_idxs = np.random.choice(stay_row_idxs, size=max_random_pick, replace=False)
            selected_rows.append(full_dat[selected_idxs])
        return np.concatenate(selected_rows)
    else:
        return np.concatenate([
            full_dat[patient_ids == p_stay_id] for p_stay_id in selected_ids
            ])

def main():
    args = parse_args()
    logging.basicConfig(
        format="%(message)s", filename=args.log_file, level=logging.INFO
    )
    # parameters
    np.random.seed(args.seed)

    # Prep data
    dat = pd.concat([
        pd.read_csv(dat_file, delimiter=",")
        for dat_file in args.dat_files])
    patient_ids = dat["patient_ID"].to_numpy()
    dat = dat.iloc[:,np.concatenate([[4], np.arange(12, dat.shape[1])])].to_numpy()
    col_vars = np.var(dat, axis=0)
    dat = dat[:, col_vars > 0]
    print("keep cols", np.sum(col_vars > 0))

    # normalize
    col_means = np.mean(dat[:,1:MAX_VARIABLES], axis=0, keepdims=True)
    col_sds = np.sqrt(np.var(dat[:,1:MAX_VARIABLES], axis=0, keepdims=True))
    dat[:,1:MAX_VARIABLES] = (dat[:,1:MAX_VARIABLES] - col_means)/col_sds

    # Shuffle patient ids
    num_uniq_ids = np.unique(patient_ids).size
    print("NUM UNIQ", num_uniq_ids)
    logging.info("uniq patient ids %d, train %d", num_uniq_ids, args.init_train_n)
    rand_ids = np.random.choice(np.unique(patient_ids), num_uniq_ids, replace=False)
    init_train_idxs = rand_ids[:args.init_train_n]
    init_train_dat = _get_data(dat, patient_ids, init_train_idxs, max_random_pick=args.random_pick_n)
    start_idx = args.init_train_n
    reuse_test_idxs = rand_ids[start_idx: start_idx + args.reuse_test_n]
    reuse_test_dat = _get_data(dat, patient_ids, reuse_test_idxs, max_random_pick=args.random_pick_n)
    start_idx += args.reuse_test_n
    logging.info("num reuse %d", reuse_test_idxs.size)
    assert reuse_test_idxs.size == args.reuse_test_n

    # Split data
    init_train_dat = Dataset(
            x=init_train_dat[:,1:MAX_VARIABLES],
            y=init_train_dat[:,:1],
            )
    reuse_test_dat = Dataset(
            x=reuse_test_dat[:,1:MAX_VARIABLES],
            y=reuse_test_dat[:,:1],
            )
    print("OUTCOME RATE", reuse_test_dat.y.mean())
    iid_train_dats = []
    for batch_start_idx in range(start_idx, rand_ids.size, args.train_batch_n):
        #batch_start_idx = start_idx + batch_idx * args.train_batch_n
        batch_ids = rand_ids[batch_start_idx: batch_start_idx + args.train_batch_n]
        dat_slice = _get_data(dat, patient_ids, batch_ids, max_random_pick=args.random_pick_n)
        iid_train_dats.append(
                Dataset(
                    x=dat_slice[:,1:MAX_VARIABLES],
                    y=dat_slice[:,:1],
            ))
    logging.info("train batches %d", len(iid_train_dats))
    assert iid_train_dats
    full_dat = FullDataset(
            init_train_dat,
            iid_train_dats,
            reuse_test_dat,
            None)
    logging.info("init train dat %d, reuse test dat size %d", init_train_dat.size, reuse_test_dat.size)

    with open(args.out_file, "wb") as f:
        pickle.dump(
            {
                "full_dat": full_dat,
            },
            f,
        )


if __name__ == "__main__":
    main()
