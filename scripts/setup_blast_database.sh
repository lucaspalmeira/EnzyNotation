#!/usr/bin/env bash
set -eo pipefail

if [[ $# -lt 2 || $# -gt 3 ]]; then
    printf 'Usage: %s PROTEINS_FASTA DATABASE_PREFIX [TITLE]\n' "$0" >&2
    exit 2
fi

proteins_fasta=$1
database_prefix=$2
database_title=${3:-EnzyNotation curated proteins}
makeblastdb_executable=${MAKEBLASTDB_BIN:-makeblastdb}

if [[ ! -f "$proteins_fasta" ]]; then
    printf 'Input FASTA does not exist: %s\n' "$proteins_fasta" >&2
    exit 1
fi

mkdir -p "$(dirname "$database_prefix")"
"$makeblastdb_executable" \
    -in "$proteins_fasta" \
    -dbtype prot \
    -parse_seqids \
    -title "$database_title" \
    -out "$database_prefix"

printf 'BLAST protein database created at prefix: %s\n' "$database_prefix"
printf '%s\n' 'Keep accession metadata in a separate TSV, CSV, or JSON file.'
