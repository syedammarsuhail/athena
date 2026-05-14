#!/usr/bin/env bash
set -e
need() {
  command -v "$1" >/dev/null 2>&1 || { echo "MISSING: $1 (install: $2)"; exit 1; }
  printf "✓ %-12s %s\n" "$1" "$($1 version 2>/dev/null | head -1 || $1 --version 2>/dev/null | head -1)"
}
echo "Athena preflight check"
echo "----------------------"
need aws       "https://docs.aws.amazon.com/cli/latest/userguide/install-cliv2.html"
need terraform "https://developer.hashicorp.com/terraform/install"
need kubectl   "https://kubernetes.io/docs/tasks/tools/"
need helm      "https://helm.sh/docs/intro/install/"
need docker    "https://docs.docker.com/get-docker/"
need git       "your package manager"
need jq        "your package manager"
echo
echo "AWS identity:"
aws sts get-caller-identity || { echo "AWS auth not configured"; exit 1; }
echo
echo "Looks good. You can start Week 1."
