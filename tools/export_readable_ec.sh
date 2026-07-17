#!/bin/sh
set -eu

if [ "$#" -lt 2 ] || [ "$#" -gt 4 ]; then
    echo "usage: $0 <64-KiB-bank.bin> <output-directory> [entry,...] [function-symbols.tsv]" >&2
    exit 2
fi

root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
input=$(realpath "$1")
output=$(realpath -m "$2")
name=$(basename "$input")
entries=${3-}
functions=${4-"$root/tools/ec_functions.tsv"}
project=$(mktemp -d "${TMPDIR:-/tmp}/ec-ghidra.XXXXXX")
config=$(mktemp -d "${TMPDIR:-/tmp}/ec-ghidra-config.XXXXXX")
cache=$(mktemp -d "${TMPDIR:-/tmp}/ec-ghidra-cache.XXXXXX")
trap 'rm -rf "$project" "$config" "$cache"' EXIT HUP INT TERM

mkdir -p "$output"
set -- ExportReadableEc.java "$root/tools/ec_registers.tsv" "$output"
if [ -n "$entries" ]; then
    set -- "$@" "$entries"
else
    set -- "$@" -
fi
set -- "$@" "$functions"

env XDG_CONFIG_HOME="$config" XDG_CACHE_HOME="$cache" \
    /opt/ghidra/support/analyzeHeadless "$project" project \
        -import "$input" -overwrite \
        -loader BinaryLoader -loader-baseAddr 0 \
        -processor 8051:BE:16:default -analysisTimeoutPerFile 120 \
        -scriptPath "$root/tools/ghidra" \
        -postScript "$@"

echo "Generated $output/$name.c and $output/ec-register-index.md"
