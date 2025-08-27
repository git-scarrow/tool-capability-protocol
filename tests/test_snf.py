import json
import pytest

from tcp.core.snf import SNFCanonicalizer, SNFError


def test_idempotence():
    c = SNFCanonicalizer()
    sel = "a//b/./c/*"
    snf1 = c.to_snf(sel)
    assert snf1 == c.to_snf(snf1)


def test_alpha_rename():
    c = SNFCanonicalizer()
    assert c.to_snf("/users/123") == "/users/{x1}"


def test_dotdot_rejected():
    c = SNFCanonicalizer()
    with pytest.raises(SNFError):
        c.to_snf("./d/../d/e/./**")
