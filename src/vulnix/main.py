"""Usage: vulnix {--system | PATH [...]}

vulnix is a tool that scan the NixOS store for packages with known
security issues. There are three main modes of operation:


* Is my NixOS system installation affected?

Invoke:  vulnix --system


* Is my project affected?

Invoke after nix-build:  vulnix ./result


See vulnix --help for a full list of options.
"""


from .nix import Store
from .nvd import NVD, DEFAULT_MIRROR, DEFAULT_CACHE_DIR
from .utils import cve_url, Timer
import click
import json
import logging
import pkg_resources
import sys
import urllib.request

CURRENT_SYSTEM = '/nix/var/nix/gcroots/current-system'

_log = logging.getLogger(__name__)


def howto():
    head, tail = __doc__.split('\n', 1)
    click.secho(head, fg='yellow')
    click.echo(tail, nl=False)


def init_logging(verbose, debug):
    if debug:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.getLogger('requests').setLevel(logging.ERROR)
        if verbose >= 2:
            logging.basicConfig(level=logging.DEBUG)
        elif verbose >= 1:
            logging.basicConfig(level=logging.INFO)
        else:
            logging.basicConfig(level=logging.WARNING)


def output_json(derivations):
    out = []
    status = 0
    for d in sorted(derivations, key=lambda k: k.pname):
        out.append({
            'name': d.name,
            'pname': d.pname,
            'version': d.version,
            'derivation': d.store_path,
            'affected_by': list(d.affected_by),
        })
        status = max([status, 2])
    print(json.dumps(out, indent=1))
    return status


def output(derivations, verbosity):
    status = 0
    derivations = sorted(derivations, key=lambda k: k.pname)

    amount = len(derivations)
    if amount == 0:
        summary = 'Found no advisories'
    else:
        names = ', '.join(d.pname for d in derivations[:3])
        summary = 'Found {} advisories for {}'.format(amount, names)
        if amount > 3:
            summary += ', ... (and {:d} more)'.format(amount - 3)
    click.secho(summary, fg='red')

    for derivation in derivations:
        click.echo('\n{}'.format('=' * 72))
        click.secho('{}\n'.format(derivation.name), fg='yellow')
        if verbosity >= 1:
            click.secho(derivation.store_path, fg='magenta')
        click.echo("CVEs:")
        for cve_id in derivation.affected_by:
            click.echo("\t" + cve_url(cve_id))
        status = max([status, 2])

    return status


def populate_store(gc_roots, paths, requisites=True):
    """Load derivations from nix store depending on cmdline invocation."""
    store = Store(requisites)
    if gc_roots:
        store.add_gc_roots()
    for path in paths:
        store.add_path(path)
    return store


class Resource:

    def __init__(self, url):
        self.url = url
        if self.url.startswith('http'):
            # http ressource
            try:
                self.fp = urllib.request.urlopen(url)
            except:
                _log.debug("Couldn't open: {}".format(self.url))
        else:
            # local file ressource
            self.fp = open(url)


def open_resource(ctx, param, value):
    """returns fp for files or remote ressources"""
    if value:
        for v in value:
            yield Resource(v)


def run(nvd, store):
    affected = set()
    for derivation in store.derivations.values():
        derivation.check(nvd)
        if derivation.is_affected:
            affected.add(derivation)
    return affected


def filter_wl(whitelist, affected):
    # XXX
    return affected


@click.command('vulnix')
# what to scan
@click.option('-S', '--system', is_flag=True,
              help='Scan the current system')
@click.option('-G', '--gc-roots', is_flag=True,
              help='Scan all active GC roots (including old ones)')
@click.argument('path', nargs=-1, type=click.Path(exists=True))
# modify operation
@click.option('-w', '--whitelist', multiple=True, callback=open_resource,
              help='Add another whitelist ressource to declare exceptions.')
@click.option('--default-whitelist/--no-default-whitelist', default=True,
              help='(kept for compatibility reasons)')
@click.option('-c', '--cache-dir', type=click.Path(file_okay=False),
              default=DEFAULT_CACHE_DIR,
              help='Cache directory to store parsed archive data. '
              'Default: {}'.format(DEFAULT_CACHE_DIR))
@click.option('-r/-R', '--requisites/--no-requisites', default=True,
              help='Determine transitive closure vs. just examine passed '
              'derivations (default: yes)')
@click.option('-m', '--mirror',
              help='Mirror to fetch NVD archives from. Default: {}'.format(
                  DEFAULT_MIRROR),
              default=DEFAULT_MIRROR)
# output control
@click.option('-d', '--debug', is_flag=True,
              help='Show debug information.')
@click.option('-v', '--verbose', count=True,
              help='Increase output verbosity.')
@click.option('-V', '--version', is_flag=True,
              help='Print vulnix version and exit')
@click.option('-j', '--json/--no-json', help='JSON vs. human readable output')
@click.option('-F', '--notfixed', is_flag=True,
              help='(kept for compatibility reasons)')
def main(debug, verbose, whitelist, default_whitelist, gc_roots, system, path,
         mirror, cache_dir, version, requisites, json, notfixed):
    if version:
        print('vulnix ' + pkg_resources.get_distribution('vulnix').version)
        sys.exit(0)

    if not (gc_roots or system or path):
        howto()
        sys.exit(3)

    init_logging(verbose, debug)

    paths = list(path)
    if system:
        paths.append(CURRENT_SYSTEM)

    try:
        with Timer('Load whitelist'):
            # XXX
            whitelist = []

        with Timer('Load derivations'):
            store = populate_store(gc_roots, paths, requisites)

        nvd = NVD(mirror, cache_dir)
        with nvd:
            with Timer('Load NVD data'):
                nvd.update()
            with Timer('Scan vulnerabilities'):
                deriv = filter_wl(whitelist, run(nvd, store))

        if json:
            sys.exit(output_json(deriv))
        sys.exit(output(deriv, verbose))

    # This needs to happen outside the NVD context: otherwise ZODB will abort
    # the transaction and we will keep updating over and over.
    except RuntimeError as e:
        _log.critical(e)
        sys.exit(2)
