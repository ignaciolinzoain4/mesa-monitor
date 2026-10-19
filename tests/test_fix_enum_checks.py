from tools.fix_enum_checks import fix

SAMPLE = """    op.create_table(
        "x",
        sa.Column("s", sa.Enum("a", "b", name="st", native_enum=False, create_constraint=True)),
        sa.CheckConstraint("s IN ('a', 'b')", name='st'),
        sa.CheckConstraint(
            "s IN ('a', 'b')",
            name="st",
        ),
        sa.CheckConstraint("s IN ('a', 'b')", name=op.f("ck_x_st")),
    )
"""


def test_removes_plain_checks_keeps_convention_ones() -> None:
    out = fix(SAMPLE)
    assert out.count("CheckConstraint") == 1
    assert 'op.f("ck_x_st")' in out
    assert "create_constraint=False" in out
