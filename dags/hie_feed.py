"""
Turns Synthea's FHIR export into a plausible *outside* feed.

Synthea emits CSV and FHIR for the same simulated population using the same
UUIDs throughout, so joining the two paths is a trivial key equality. That is
not how healthcare data arrives. A feed from a health information exchange,
an acquired practice, or a referring provider carries that organization's own
identifiers and its own copy of the demographics -- entered by different staff,
at a different time, under a different data-entry standard. There is no shared
key, and the demographics disagree.

This module simulates that gap. It rewrites every identifier in the FHIR path
into an opaque HIE-local id and perturbs the demographics, so the pipeline has
to resolve identity by probabilistic matching (see the dbt EMPI models) rather
than by joining on a UUID.

Every transformation is a pure function of the source UUID, so repeated runs
produce a byte-identical feed -- the pipeline stays reproducible, and the
match rate is a property of the matching logic rather than of the weather.
"""

import hashlib

# Fixed salt: changing it reshuffles every identifier and every perturbation
# decision, which would invalidate an existing MPI. Treat it as a constant.
_SALT = 'healthcare-de/hie-feed/v1'


def _digest(*parts: str) -> int:
    """Stable 64-bit integer derived from the given strings."""
    key = _SALT + '|' + '|'.join(p or '' for p in parts)
    return int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], 'big')


def _roll(uuid: str, field: str) -> int:
    """Deterministic 0-999 'dice roll' for one (record, decision) pair."""
    return _digest(uuid, field) % 1000


def hie_id(prefix: str, uuid: str | None) -> str | None:
    """
    Map a Synthea UUID onto an opaque identifier local to the sending system.

    The source UUID is not recoverable from the result and is not carried
    anywhere in the feed -- that is the point. Downstream has nothing to join
    on but names, dates and addresses.
    """
    if not uuid:
        return None
    return f'{prefix}-{hashlib.sha256((_SALT + uuid).encode()).hexdigest()[:12].upper()}'


def _transpose(text: str, uuid: str, field: str) -> str:
    """Swap an adjacent pair of letters -- the most common keyboard typo."""
    letters = [i for i, c in enumerate(text) if c.isalpha()]
    if len(letters) < 2:
        return text
    i = letters[_roll(uuid, field + ':pos') % (len(letters) - 1)]
    chars = list(text)
    chars[i], chars[i + 1] = chars[i + 1], chars[i]
    return ''.join(chars)


def _swap_month_day(birth_date: str | None, uuid: str) -> str | None:
    """
    Reverse the month and day of a birth date.

    A US-format date keyed into a system expecting day-first (or the reverse)
    is one of the most common real sources of demographic conflict, and it only
    produces a visible error when both components are 12 or under.
    """
    if not birth_date or len(birth_date) != 10:
        return birth_date
    year, month, day = birth_date[:4], birth_date[5:7], birth_date[8:10]
    if int(day) > 12 or int(day) == 0:
        # Not transposable without producing an invalid date; nudge the year
        # instead, standing in for a mis-keyed digit. February 29th is left
        # alone -- shifting its year would yield a date that does not exist.
        if (month, day) == ('02', '29'):
            return birth_date
        offset = 1 if _roll(uuid, 'dob:year-dir') < 500 else -1
        return f'{int(year) + offset:04d}-{month}-{day}'
    return f'{year}-{day}-{month}'


def perturb_patient(record: dict, uuid: str) -> dict:
    """
    Apply the demographic drift an outside feed would carry.

    Rates are deliberately higher than a well-run interface would show, so a
    1,000-patient population still exercises every matching branch. Each
    decision is independent and seeded per record, so a patient can pick up
    several discrepancies at once -- which is what makes the hard tail of the
    match score distribution non-empty.
    """
    out = dict(record)

    # Different systems standardize generated name tokens differently; one
    # keeps Synthea's numeric suffix, another strips it. Harmless once names
    # are normalized downstream, which is exactly what it is here to show.
    if _roll(uuid, 'name:suffix') < 220:
        for field in ('family_name', 'given_name'):
            if out.get(field):
                out[field] = ''.join(c for c in out[field] if not c.isdigit())

    if _roll(uuid, 'family:typo') < 70 and out.get('family_name'):
        out['family_name'] = _transpose(out['family_name'], uuid, 'family:typo')

    if _roll(uuid, 'given:typo') < 50 and out.get('given_name'):
        out['given_name'] = _transpose(out['given_name'], uuid, 'given:typo')

    # Married name recorded at one site but not the other.
    if _roll(uuid, 'family:hyphen') < 40 and out.get('family_name'):
        spouse = f"Marquez{_digest(uuid, 'spouse') % 900 + 100}"
        out['family_name'] = f"{out['family_name']}-{spouse}"

    if _roll(uuid, 'given:initial') < 45 and out.get('given_name'):
        out['given_name'] = out['given_name'][:1]

    if _roll(uuid, 'dob') < 60:
        out['birth_date'] = _swap_month_day(out.get('birth_date'), uuid)

    # Outside feeds are routinely missing address and sex.
    if _roll(uuid, 'zip') < 120:
        out['postal_code'] = None
    elif _roll(uuid, 'zip:stale') < 90 and out.get('postal_code'):
        # The exchange holds an address the patient has since moved away from,
        # or a mis-keyed one. Without this the two feeds could only ever agree
        # or be silent about the postal code, and a disagreement on address --
        # the most common demographic conflict there is -- would never arise.
        nearby = f"{(int(out['postal_code']) + _digest(uuid, 'zip:delta') % 40 + 1) % 100000:05d}"
        out['postal_code'] = nearby
    if _roll(uuid, 'gender') < 35:
        out['gender'] = 'unknown'

    # Race and ethnicity are collected under different value sets per system
    # and are among the least reliable fields in practice.
    if _roll(uuid, 'race') < 150:
        out['race'] = None

    return out


def feed_role(uuid: str) -> str:
    """
    Decide whether a patient appears in the HIE feed at all, and how.

    Real exchanges are not mirrors of the local EHR. Some local patients have
    no outside record; some outside records are so sparse that no responsible
    algorithm will match them, leaving them to sit as distinct persons in the
    master index until someone reviews them.

      'absent'    -- not in the feed: becomes an EHR-only patient
      'orphan'    -- in the feed, identity scrubbed: becomes an HIE-only patient
      'duplicate' -- in the feed twice, under two local ids
      'normal'    -- in the feed once
    """
    roll = _roll(uuid, 'role')
    if roll < 25:
        return 'absent'
    if roll < 50:
        return 'orphan'
    if roll < 80:
        return 'duplicate'
    return 'normal'


def orphan_identity(record: dict, uuid: str) -> dict:
    """
    Replace the identifying fields outright.

    Stands in for a record the exchange holds under a name and date of birth
    that share nothing with the local chart -- a wrong-patient registration, a
    newborn filed under a placeholder, a transcription from paper.
    """
    out = dict(record)
    n = _digest(uuid, 'orphan')
    out['family_name'] = f'Unmatched{n % 9000 + 1000}'
    out['given_name'] = f'Record{n // 9000 % 9000 + 1000}'
    out['birth_date'] = f'{1930 + n % 90}-{n % 12 + 1:02d}-{n % 28 + 1:02d}'
    out['postal_code'] = None
    return out


def duplicate_suffix(uuid: str) -> str:
    """Second local id for a patient the sending system holds twice."""
    return hashlib.sha256((_SALT + 'dup' + uuid).encode()).hexdigest()[:12].upper()
