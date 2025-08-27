"""Minimal command line interface for TCP utilities."""
from __future__ import annotations

import binascii
import hashlib
import pathlib
import sys

import click

from .core import signing_surface
from .core.snf import SNFCanonicalizer, SNFError


@click.group()
def main() -> None:
    """TCP helper commands."""
    pass


@main.command()
@click.argument("selector")
def snf(selector: str) -> None:
    """Print the SNF string and hash key for SELECTOR."""
    c = SNFCanonicalizer()
    try:
        snf_str = c.to_snf(selector)
    except SNFError as exc:
        click.echo(f"error: {exc}", err=True)
        sys.exit(1)
    key_hex = c.key(selector).hex()
    click.echo(snf_str)
    click.echo(key_hex)


@main.command("verify-signature")
@click.argument("descriptor", type=click.Path(exists=True, dir_okay=False))
def verify_signature_cmd(descriptor: str) -> None:
    """Print hash of descriptor without evidence TLVs."""
    data = pathlib.Path(descriptor).read_bytes()
    canonical = signing_surface.canonical_bytes_without_evidence(data)
    digest = binascii.hexlify(hashlib.sha256(canonical).digest()).decode()
    click.echo(digest)


if __name__ == "__main__":  # pragma: no cover
    main()
