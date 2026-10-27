{#
    Standardization helpers shared by both ingestion paths.

    The local EHR extract and the HIE feed describe the same people using
    different vocabularies and different data-entry conventions. Comparing
    them without first putting both into one representation produces a match
    rate that measures the formatting difference rather than the identities.
#}

{% macro normalize_gender(column) %}
    {#
        The CSV extract carries administrative sex as 'M'/'F'; the FHIR feed
        uses the FHIR administrative-gender value set ('male', 'female',
        'other', 'unknown'). Anything unrecognized becomes 'unknown' rather
        than null, so the match logic can treat "we were told nothing" as a
        distinct case from "the systems disagree".
    #}
    case lower(trim(cast({{ column }} as varchar)))
        when 'm'      then 'male'
        when 'male'   then 'male'
        when 'f'      then 'female'
        when 'female' then 'female'
        when 'other'  then 'other'
        else 'unknown'
    end
{% endmacro %}


{% macro normalize_name(column) %}
    {#
        Strip everything that is not a letter and upper-case the rest.

        Synthea appends a numeric token to every generated name
        ('Wilderman619'), and one of the two feeds drops it. Punctuation,
        spacing, and case vary independently. None of that is identity, so
        none of it should reach the comparison.
    #}
    upper(regexp_replace(coalesce(cast({{ column }} as varchar), ''), '[^A-Za-z]', ''))
{% endmacro %}


{% macro name_root(column, delimiter) %}
    {#
        First component of a multi-part name: the surname before a hyphen, or
        the given name before a middle name. A married name recorded at one
        site and not the other still agrees on its root, so comparing roots
        alongside whole names recovers matches that whole-name comparison
        alone would drop.
    #}
    {{ normalize_name("split_part(coalesce(cast(" ~ column ~ " as varchar), ''), '" ~ delimiter ~ "', 1)") }}
{% endmacro %}


{% macro safe_soundex(column) %}
    {# soundex() rejects an empty string, which normalization can produce. #}
    case when length({{ column }}) > 0 then soundex({{ column }}) end
{% endmacro %}


{% macro clean_postal_code(column) %}
    {#
        Blank out the placeholders that mean "we don't know" rather than an
        address. Synthea emits 00000 for a patient with no usable address, and
        real extracts are full of equivalents -- 99999, 00000, a single zero.
        Left as-is, every such patient agrees on postal code with every other,
        which hands the matcher 15 points of evidence for a pairing that has
        none. A sentinel treated as data is worse than a null, because a null
        at least scores zero.
    #}
    nullif(nullif(nullif(trim(cast({{ column }} as varchar)), ''), '00000'), '99999')
{% endmacro %}
