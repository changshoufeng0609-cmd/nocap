"""Release self-names resolve on the existing snapshot, without re-ingest."""
import pytest

from receipts import store as st
from test_entity_scale import make_store


@pytest.fixture(scope='module')
def snapshot():
    s = st.Store(st.ROOT / 'data/snapshot/facts.sqlite')
    yield s
    s.close()


# Every corrected miss in the archived-release sweep (16 distinct issuer/name pairs).
RELEASE_MISSES = [
    ('BDX', 'BD'), ('CASY', 'Casey’s General Stores, Inc.'),
    ('CHRW', 'C.H. Robinson Worldwide, Inc.'), ('COO', 'CooperCompanies'),
    ('DHI', 'D.R. Horton, Inc.'), ('DPZ', 'Domino’s Pizza, Inc.'),
    ('FCX', 'Freeport'), ('GWW', 'Grainger'),
    ('JBHT', 'J.B. Hunt Transport Services, Inc.'), ('LOW', 'Lowe’s Companies, Inc.'),
    ('MPWR', 'MPS'), ('MRSH', 'Marsh'), ('ODFL', 'Old Dominion'),
    ('ON', 'onsemi'), ('SBAC', 'SBA'), ('WM', 'WM'),
]


@pytest.mark.parametrize('ticker,name', RELEASE_MISSES)
def test_release_misses_resolve_without_rebuilding(snapshot, ticker, name):
    assert snapshot.resolve_entity(name) == snapshot.by_ticker(ticker)


@pytest.mark.parametrize('ticker,name', [
    ('LOW', "Lowe's Companies, Inc."), ('MCD', "McDonald's Corporation"),
    ('MCO', "Moody's Corporation"), ('ORLY', "O'Reilly Automotive"),
    ('DPZ', "Domino's Pizza"),
])
@pytest.mark.parametrize('curly', [False, True])
def test_internal_apostrophes_preserve_name_letters(snapshot, ticker, name, curly):
    if curly:
        name = name.replace("'", '’')
    assert snapshot.resolve_entity(name) == snapshot.by_ticker(ticker)
    assert [t for _, t, _ in snapshot.names_in_text(f'{name} reported results.')] == [ticker]


@pytest.mark.parametrize('ticker,initials,name', [
    ('DHI', 'DR', 'Horton'), ('SJM', 'JM', 'Smucker'), ('CHRW', 'CH', 'Robinson'),
    ('AOS', 'AO', 'Smith'), ('WRB', 'WR', 'Berkley'), ('GWW', 'WW', 'Grainger'),
    ('JBHT', 'JB', 'Hunt'),
])
@pytest.mark.parametrize('style', ['joined', 'dotted', 'spaced', 'spaced_dotted'])
def test_initials_have_one_exact_key(snapshot, ticker, initials, name, style):
    prefix = {'joined': initials, 'dotted': '.'.join(initials)+'.',
              'spaced': ' '.join(initials), 'spaced_dotted': '. '.join(initials)+'.'}[style]
    written = f'{prefix} {name}'
    assert snapshot.resolve_entity(written) == snapshot.by_ticker(ticker)
    assert [t for _, t, _ in snapshot.names_in_text(f'{written} reported results.')] == [ticker]


@pytest.mark.parametrize('name', [
    'Amazon Web Services', 'Google Cloud', 'SBA loans', 'Prudential plc', 'the carrier',
    'the SBA', 'SBA Corporation', 'Old Dominion University', 'DR Horton plc', 'D.R. Hort',
    'Honeywell', 'The Hartford', 'Dow',
])
def test_segments_prose_suffixes_and_ambiguous_names_stay_unresolved(snapshot, name):
    assert snapshot.resolve_entity(name) is None


def test_release_short_names_are_bare_only_even_for_a_proposed_company(snapshot):
    for ticker, names in st.RELEASE_SHORT_NAMES.items():
        for name in names:
            text = f'{name} loans increased at the bank.'
            assert not snapshot.names_in_text(text)
            s, tokens = st.scan_tokens(text)
            assert not st.find_mentions(s, tokens, snapshot._index(), proposed=snapshot.by_ticker(ticker)[0])
    assert [t for _, t, _ in snapshot.names_in_text('Bank of America expanded SBA loans.')] == ['BAC']


def test_legacy_stored_alias_initials_and_sec_possessive_are_rekeyed(tmp_path):
    # Synthetic companies: the fix cannot depend on the curated company tables.
    s = make_store(tmp_path, {'ZQX': "Zane's Widgets Inc.", 'QRX': 'Q R Tools Inc.'},
                   aliases=lambda t, n: {'ZQX': {'zane widgets'}, 'QRX': {'q r tools', 'q r toolworks'}}[t])
    try:
        assert s.resolve_entity("Zane’s Widgets, Inc.") == s.by_ticker('ZQX')
        assert s.resolve_entity('Q.R. Toolworks') == s.by_ticker('QRX')
        assert s.resolve_entity('QR Tools Inc.') == s.by_ticker('QRX')
        assert s.resolve_entity('QR Tools plc') is None
    finally:
        s.close()


def test_new_canonical_keys_preserve_collisions(tmp_path):
    s = make_store(tmp_path, {'ZQX': 'Z Q Tools Inc.', 'ZQY': 'ZQ Tools Inc.'},
                   aliases=lambda t, n: {'ZQX': {'z q tools'}, 'ZQY': {'zq tools'}}[t])
    try:
        for name in ('Z.Q. Tools', 'Z Q Tools', 'ZQ Tools', 'ZQ Tools Inc.'):
            assert s.resolve_entity(name) is None
            assert not s.names_in_text(f'{name} reported results.')
    finally:
        s.close()


@pytest.mark.parametrize('name', ["Nvidia's", "Nvidia's.", 'Nvidia’s,', "Nvidia Corporation's"])
def test_closing_possessives_still_resolve(snapshot, name):
    assert snapshot.resolve_entity(name) == snapshot.by_ticker('NVDA')
