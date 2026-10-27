#!/usr/bin/env python3
"""
Emit a late-arriving correction feed for an existing Synthea extract.

Synthea produces one immutable snapshot of a finished simulation. Source
systems do not behave that way. A encounter is coded, billed, and then
re-coded a week later when a chart review lands; a claim is repriced when the
payer responds; a discharge time is corrected; a whole encounter is voided
because it was filed against the wrong patient. Demographics move too --
people change address, change surname, and have data-entry errors fixed.

Those corrections arrive *after* the row was already loaded, and they keep
arriving for months. An ingest that only ever appends, or that rebuilds the
table from the latest full extract, silently loses the fact that the number
changed and cannot answer "what did we believe on the day we reported it."

This script writes a timestamped delta file per run into data/delta/. Each
run is a fresh batch of corrections against rows the warehouse already holds,
which is what gives the pipeline something to MERGE rather than replace.

Usage:
    python scripts/simulate_amendments.py                 # one batch, default rates
    python scripts/simulate_amendments.py --seed 42       # reproducible batch
    python scripts/simulate_amendments.py --amend-rate 0.05
"""

import argparse
import csv
import os
import random
import sys
from datetime import datetime, timedelta, timezone

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Synthea writes ./data as root from inside its container, so this script runs
# containerized too (`make simulate-amendments`) and is pointed at the mount.
# The default keeps it runnable directly from a checkout.
DATA_DIR = os.environ.get('HEALTHCARE_DATA_DIR', os.path.join(REPO, 'data'))
CSV_DIR = os.path.join(DATA_DIR, 'csv')
DELTA_DIR = os.path.join(DATA_DIR, 'delta')

# Correction kinds, with the weight each carries in a batch. Repricing
# dominates in real claims traffic; voids are rare but the most important to
# handle, because an append-only load double-counts them forever.
ENCOUNTER_CHANGES = [
    ('reprice', 50),
    ('recode', 25),
    ('correct_discharge', 20),
    ('void', 5),
]

# An encounter re-coded to a different setting of care. These are the
# corrections that move money, which is why they get audited.
CLASS_RECODE = {
    'emergency': 'inpatient',
    'ambulatory': 'outpatient',
    'outpatient': 'ambulatory',
    'urgentcare': 'emergency',
    'wellness': 'ambulatory',
    'inpatient': 'emergency',
}

PATIENT_CHANGES = [
    ('moved', 60),
    ('name_change', 30),
    ('dob_correction', 10),
]

STREETS = ['Oak Ridge Ave', 'Beacon St', 'Harborview Rd', 'Elm Hollow Ln',
           'Quarry Point Dr', 'Sycamore Row', 'Winslow Ct']
TOWNS = [('Somerville', '02143'), ('Revere', '02151'), ('Waltham', '02451'),
         ('Brockton', '02301'), ('Lowell', '01851'), ('Chicopee', '01013')]


def _weighted(rng, choices):
    population, weights = zip(*choices)
    return rng.choices(population, weights=weights, k=1)[0]


def _read(name):
    path = os.path.join(CSV_DIR, name)
    if not os.path.exists(path):
        sys.exit(f"error: {path} not found -- run `make generate-data` first")
    with open(path, newline='') as f:
        reader = csv.DictReader(f)
        return reader.fieldnames, list(reader)


def _write(name, fieldnames, rows, stamp):
    os.makedirs(DELTA_DIR, exist_ok=True)
    path = os.path.join(DELTA_DIR, f'{name}_{stamp}.csv')
    with open(path, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames + ['CHANGE_TYPE', 'SOURCE_UPDATED_AT'])
        writer.writeheader()
        writer.writerows(rows)
    print(f'  {len(rows):>6} rows -> {os.path.relpath(path, REPO)}')
    return path


def amend_encounters(rng, rows, rate, updated_at):
    """Sample encounters and restate them. Returns the corrected rows only."""
    sampled = rng.sample(rows, k=max(1, int(len(rows) * rate)))
    out = []

    for row in sampled:
        row = dict(row)
        kind = _weighted(rng, ENCOUNTER_CHANGES)

        if kind == 'void':
            # A void keeps the original values and flags the row. Claims are
            # never deleted -- the reversal is itself part of the audit trail.
            row['CHANGE_TYPE'] = 'void'

        elif kind == 'reprice':
            # Payers reprice against the contracted rate, usually downward.
            try:
                cost = float(row['TOTAL_CLAIM_COST'] or 0)
            except ValueError:
                continue
            factor = rng.uniform(0.55, 1.35)
            row['TOTAL_CLAIM_COST'] = f'{cost * factor:.2f}'
            if row.get('PAYER_COVERAGE'):
                try:
                    row['PAYER_COVERAGE'] = f"{float(row['PAYER_COVERAGE']) * factor:.2f}"
                except ValueError:
                    pass
            row['CHANGE_TYPE'] = 'update'

        elif kind == 'recode':
            current = (row.get('ENCOUNTERCLASS') or '').lower()
            if current not in CLASS_RECODE:
                continue
            row['ENCOUNTERCLASS'] = CLASS_RECODE[current]
            row['CHANGE_TYPE'] = 'update'

        else:  # correct_discharge
            stop, start = row.get('STOP'), row.get('START')
            if not stop or not start:
                continue
            try:
                parsed = datetime.fromisoformat(stop.replace('Z', '+00:00'))
                began = datetime.fromisoformat(start.replace('Z', '+00:00'))
            except ValueError:
                continue
            offset = rng.choice([-180, -60, -15, 15, 60, 240, 1440])
            shifted = parsed + timedelta(minutes=offset)
            # A corrected discharge still has to come after the admission.
            # Short encounters are the common case here -- a 15-minute office
            # visit shifted back an hour would end before it began -- so a
            # negative offset that would invert the interval is applied
            # forwards instead. Genuinely impossible intervals do turn up in
            # real extracts, but they belong to a data-quality layer, not to a
            # generator that is meant to emit plausible corrections.
            if shifted < began:
                shifted = parsed + timedelta(minutes=abs(offset))
            row['STOP'] = shifted.isoformat().replace('+00:00', 'Z')
            row['CHANGE_TYPE'] = 'update'

        row['SOURCE_UPDATED_AT'] = updated_at
        out.append(row)

    return out


def amend_patients(rng, rows, rate, updated_at):
    """Sample patients and restate their demographics."""
    sampled = rng.sample(rows, k=max(1, int(len(rows) * rate)))
    out = []

    for row in sampled:
        row = dict(row)
        kind = _weighted(rng, PATIENT_CHANGES)

        if kind == 'moved':
            town, zip_code = rng.choice(TOWNS)
            row['ADDRESS'] = f'{rng.randint(1, 1400)} {rng.choice(STREETS)}'
            row['CITY'] = town
            row['ZIP'] = zip_code

        elif kind == 'name_change':
            row['LAST'] = f'{rng.choice(["Okonkwo", "Baptiste", "Lindqvist", "Ferraro", "Nakamura"])}{rng.randint(100, 999)}'

        else:  # dob_correction -- an original mis-keyed entry being fixed
            try:
                birth = datetime.fromisoformat(row['BIRTHDATE'])
            except (ValueError, KeyError):
                continue
            row['BIRTHDATE'] = (birth + timedelta(days=rng.choice([-366, -31, -1, 1, 31, 366]))).date().isoformat()

        row['CHANGE_TYPE'] = 'update'
        row['SOURCE_UPDATED_AT'] = updated_at
        out.append(row)

    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--amend-rate', type=float, default=0.03,
                        help='share of encounters restated in this batch (default 0.03)')
    parser.add_argument('--patient-rate', type=float, default=0.02,
                        help='share of patients whose demographics change (default 0.02)')
    parser.add_argument('--seed', type=int, default=None,
                        help='seed for a reproducible batch; omit for a new random batch')
    args = parser.parse_args()

    rng = random.Random(args.seed)
    now = datetime.now(timezone.utc)
    updated_at = now.isoformat(timespec='seconds').replace('+00:00', 'Z')
    stamp = now.strftime('%Y%m%dT%H%M%SZ')

    print(f'Correction batch {stamp} (source_updated_at={updated_at})')

    enc_fields, encounters = _read('encounters.csv')
    pat_fields, patients = _read('patients.csv')

    _write('encounters', enc_fields,
           amend_encounters(rng, encounters, args.amend_rate, updated_at), stamp)
    _write('patients', pat_fields,
           amend_patients(rng, patients, args.patient_rate, updated_at), stamp)

    print('Re-run the pipeline to MERGE these corrections into Iceberg.')


if __name__ == '__main__':
    main()
