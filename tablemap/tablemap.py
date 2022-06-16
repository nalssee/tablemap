import csv
import locale
import os
import random
import signal
import sqlite3
import string
from contextlib import contextmanager
from fnmatch import fnmatch
from inspect import signature
from itertools import groupby
from shutil import copyfile

import pandas as pd
import psutil
from graphviz import Digraph
from more_itertools import chunked, spy
from openpyxl import Workbook
from pathos.multiprocessing import ProcessingPool as Pool
from sas7bdat import SAS7BDAT
from tqdm import tqdm

from .config import _RESERVED_KEYWORDS, _TEMP
from .exceptions import (GraphvizNotInstalled, InvalidColumns, InvalidGroup,
                         NoRowToInsert, NoRowToWrite, NoSuchTableFound,
                         ReservedKeyword, SkipThisTurn, UnknownCommand)
from .logging import logger
from .util import _build_keyfn, listify, step


def map(fn, table, by=None, parallel=False):
    d = {}
    d['cmd'] = 'map'
    d['fn'] = fn
    d['inputs'] = [table.strip()]
    d['by'] = listify(by) if by else []
    d['parallel'] = parallel
    return d


def read(file, fn=None, delimiter=None, quotechar='"',
        encoding='utf-8'):
    d = {}
    if isinstance(file, str):
        d['file'] = os.path.join(os.getcwd(), file)
    else:
        # otherwise file is an iterator
        d['file'] = file
    d['cmd'] = 'read'
    d['fn'] = fn 
    d['delimiter'] = delimiter
    d['quotechar'] = quotechar 
    d['encoding'] = encoding
    d['inputs'] = []
    return d


def join(*args):
    d = {}
    d['cmd'] = 'join'
    d['inputs'] = [arg[0].strip() for arg in args] 
    d['args'] = args
    return d


def concat(tables):
    return {
        'cmd': 'concat',
        'inputs': listify(tables),
    }


def mzip(fn, data, stop_short=False):
    # matching zip for more complex joining procs
    d = {}
    d['cmd'] = 'mzip'
    d['fn'] = fn
    # data: [(table, columns_to_match), ...]
    d['inputs'] = [table.strip() for table, _ in data] 
    d['data'] = data
    d['stop_short'] = stop_short
    return d
    

@contextmanager
def _connect(db):
    conn = sqlite3.connect(db)
    conn.row_factory = _dict_factory
    try:
        yield conn
    finally:
        # Trying to make closing atomic to handle multiple ctrl-cs
        # Imagine the first ctrl-c have the process enter the 'finally block'
        # and the second ctrl-c interrupts the block in the middle
        # so that the database is corrupted
        with _delayed_keyboard_interrupts():
            conn.commit()
            conn.close()


@contextmanager
def _delayed_keyboard_interrupts():
    signal_received = []

    def handler(sig, frame):
        nonlocal signal_received
        signal_received = (sig, frame)
        logger.debug('SIGINT received. Delaying KeyboardInterrupt.')
    old_handler = signal.signal(signal.SIGINT, handler)

    try:
        yield
    finally:
        signal.signal(signal.SIGINT, old_handler)
        if signal_received:
            old_handler(*signal_received)


class Conn:
    """ Connection to a database

    :param dbfile: str (database file name) 
        Creates a new file if it doesn't exist.
        Currrent working directory is the default path.
    """
    def __init__(self, dbfile):
        locale.setlocale(locale.LC_ALL, 
            'English_United States.1252' if os.name == 'nt' else 'en_US.UTF-8')

        # cwd is the workspace where all the files are 
        self.db = os.path.join(os.getcwd(), dbfile)
        self.insts = {}

    def __setitem__(self, key, value):
        self.insts[key] = value

    def drop(self, tables):
        with _connect(self.db) as c:
            _drop(c, tables)

    def viz(self, file):
        insts = _append_output(self.insts)
        graph = _build_graph(insts)

        dot = Digraph()
        for k, v in graph.items():
            dot.node(k, k)
            if k != v:
                for v1 in v:
                    dot.edge(k, v1)
        for inst in insts:
            if inst['cmd'] == 'read':
                dot.node(inst['output'], inst['output'])
        try:
            dot.render(os.path.join(os.getcwd(), file))
        except:
            raise GraphvizNotInstalled

    def get(self, tname, cols=None):
        # cols: order by these columns
        with _connect(self.db) as c:
            tname = tname.strip()
            if tname in _ls(c):
                if cols:
                    sql = f"""select * from {tname}
                            order by {','.join(listify(cols))}"""
                else:
                    sql = f"select * from {tname}"
                yield from _fetch(c, sql) 

            # It's possible to execute once the other insts are done
            elif tname in self.insts:
                raise SkipThisTurn
            else:
                raise NoSuchTableFound(tname) 

    def export(self, tables):
        # tables: str of table names 
        # if you want it to be an excel file then 'table1.xlsx' 
        # otherwise they will be all csvs
        with _connect(self.db) as c:
            for table in listify(tables):
                table, ext = os.path.splitext(table)
                if ext.lower() == '.xlsx':
                    rs = _fetch(c, f'select * from {table}')
                    book = Workbook()
                    sheet = book.active
                    r0, rs = spy(rs)
                    header = list(r0[0])
                    sheet.append(header)
                    for r in rs:
                        sheet.append(list(r.values()))
                    book.save(os.path.join(os.getcwd(), f'{table}.xlsx'))

                else:
                    with open(os.path.join(os.getcwd(), table + '.csv'), 'w',
                              encoding='utf-8', newline='') as f:
                        rs = _fetch(c, f'select * from {table}')
                        r0, rs = spy(rs)
                        if r0 == []:
                            raise NoRowToWrite
                        fieldnames = list(r0[0])
                        writer = csv.DictWriter(f, fieldnames=fieldnames)
                        writer.writeheader()
                        for r in rs:
                            writer.writerow(r)

    def run(self):
        # todo: Do we need the option to make it quiet, not sure yet
        logger.propagate = True
        insts = _append_output(self.insts)
        required_tables = _find_required_tables(insts)

        with _connect(self.db) as c:
            def delete_after(missing_table, paths):
                for path in paths:
                    if missing_table in path:
                        for x in path[path.index(missing_table):]:
                            _drop(c, x)

            def get_missing_tables():
                existing_tables = _ls(c)
                return [table for table in required_tables
                        if table not in existing_tables]

            def find_insts_to_do(insts):
                missing_tables = get_missing_tables()
                result = []
                for inst in insts:
                    for table in inst['inputs'] + [inst['output']]:
                        if table in missing_tables:
                            result.append(inst)
                            break
                return result

            def is_doable(inst):
                missing_tables = get_missing_tables()
                return all(table not in missing_tables for table in inst['inputs'])\
                    and inst['output'] in missing_tables

            graph = _build_graph(insts)

            starting_points = [inst['output']
                               for inst in insts if inst['cmd'] == 'read']
            paths = []
            for sp in starting_points:
                paths += _dfs(graph, [sp], [])

            for mt in get_missing_tables():
                delete_after(mt, paths)

            insts_to_do = find_insts_to_do(insts)
            initial_insts_to_do = list(insts_to_do)
            logger.info(f'To Create: {[j["output"] for j in insts_to_do]}')

            while insts_to_do:
                cnt = 0
                for i, inst in enumerate(insts_to_do):
                    if is_doable(inst):
                        try:
                            if inst['cmd'] != 'apply':
                                logger.info(
                                    f"processing {inst['cmd']}: {inst['output']}")
                            _execute(c, inst)

                        except SkipThisTurn:
                            continue

                        except Exception as e:

                            if isinstance(e, NoRowToInsert):
                                # Many times you want it to be silenced
                                # because you want to test it before actually
                                # write the code
                                logger.warning(
                                    f"No row to insert: {inst['output']}")
                            else:
                                logger.error(f"Failed: {inst['output']}")
                                logger.error(f"{type(e).__name__}: {e}",
                                             exc_info=True)

                            try:
                                _drop(c, inst['output'])
                            except Exception:
                                pass

                            logger.warning(
                                f"Unfinished: "
                                f"{[inst['output'] for inst in insts_to_do]}")
                            return (initial_insts_to_do, insts_to_do)
                        del insts_to_do[i]
                        cnt += 1
                # No insts can be done anymore
                if cnt == 0:
                    for j in insts_to_do:
                        logger.warning(f'Unfinished: {j["output"]}')
                        for t in j['inputs']:
                            if t not in _ls(c):
                                logger.warning(f'Table not found: {t}')
                    return (initial_insts_to_do, insts_to_do)

            return (initial_insts_to_do, insts_to_do)


def _append_output(kwargs):
    for k, v in kwargs.items():
        v['output'] = k.strip()
    return [v for _, v in kwargs.items()]


def _find_required_tables(insts):
    tables = set()
    for inst in insts:
        for table in inst['inputs']:
            tables.add(table)
        tables.add(inst['output'])
    return tables


def _dfs(graph, path, paths=[]):
    datum = path[-1]
    if datum in graph:
        for val in graph[datum]:
            new_path = path + [val]
            paths = _dfs(graph, new_path, paths)
    else:
        paths += [path]
    return paths


def _build_graph(insts):
    graph = {}
    for inst in insts:
        for ip in inst['inputs']:
            if graph.get(ip):
                graph[ip].add(inst['output'])
            else:
                graph[ip] = {inst['output']}
    for x in graph:
        graph[x] = list(graph[x])
    return graph


def _fetch(c, query, by=None):
    if by and isinstance(by, list) and by != ['*'] and\
            all(isinstance(c, str) for c in by):
        query += " order by " + ','.join(by)
    if by:
        if isinstance(by, list):
            rows = c.cursor().execute(query)
            rows1 = (list(rs) for _, rs in
                     groupby(rows, _build_keyfn(by)))

        elif isinstance(by, int):
            rows = c.cursor().execute(query)
            rows1 = chunked(rows, by)

        else:
            raise InvalidGroup(by)

        yield from rows1
    else:
        rows = c.cursor().execute(query)
        yield from rows


def _join(conn, tinfos, name):
    # check if a colname is a reserved keyword
    newcols = []
    for _, cols, _ in tinfos:
        cols = [c.upper() for c in listify(cols)]
        for c in cols:
            if 'AS' in c:
                newcols.append(c.split('AS')[-1])
    for x in [name] + newcols:
        if _is_reserved(x):
            raise ReservedKeyword(x)

    tname0, _, mcols0 = tinfos[0]
    join_clauses = []
    for i, (tname1, _, mcols1) in enumerate(tinfos[1:], 1):
        eqs = []
        for c0, c1 in zip(listify(mcols0), listify(mcols1)):
            if c1:
                # allows expression such as 'col + 4' for 'c1',
                # for example. somewhat sneaky though
                if isinstance(c1, str):
                    eqs.append(f't0.{c0} = t{i}.{c1}')
                else:
                    # c1 comes with a binary operator like ">=", ">" ...
                    binop, c1 = c1
                    eqs.append(f't0.{c0} {binop} t{i}.{c1}')

        join_clauses.\
            append(f"left join {tname1} as t{i} on {' and '.join(eqs)}")
    jcs = ' '.join(join_clauses)

    allcols = []
    for i, (_, cols, _) in enumerate(tinfos):
        for c in listify(cols):
            if c == '*':
                allcols += [f't{i}.{c1}'
                            for c1 in _cols(conn, f'select * from {tinfos[i][0]}')]
            else:
                allcols.append(f't{i}.{c}')

    # create indices
    ind_tnames = []
    for tname, _, mcols in tinfos:
        mcols1 = list(dict.fromkeys(c if isinstance(c, str) else c[1]
                                    for c in listify(mcols) if c))
        ind_tname = tname + _random_string(10)
        # allows expression such as 'col + 4' for indexing, for example.
        # https://www.sqlite.org/expridx.html
        conn.cursor().execute(f"""
            create index {ind_tname} on {tname}({', '.join(mcols1)})""")

    query = f"""
        create table {name} as select
        {', '.join(allcols)} from {tname0} as t0 {jcs}
    """
    conn.cursor().execute(query)

    # drop indices, not so necessary
    for ind_tname in ind_tnames:
        conn.cursor().execute(f"drop index {ind_tname}")


def _cols(conn, query):
    return [c[0] for c in conn.cursor().execute(query).description]


def _size(conn, table):
    cur = conn.cursor()
    cur.execute(f"select count(*) as c from {table}")
    return cur.fetchone()['c']


def _dict_factory(cursor, row):
    d = {}
    for idx, col in enumerate(cursor.description):
        d[col[0]] = row[idx]
    return d


def _flatten(seq):
    # You could think of enhancing the performance here.
    # But I doubt that it's worth the trouble
    for x in seq:
        if isinstance(x, dict):
            yield x
        # ignores None
        elif x is not None:
            yield from x


def _applyfn(fn, seq):
    yield from _flatten(fn(rs) for rs in seq)


def _tqdm(seq, total, by):
    # you need another fn to deal with groupings
    if by:
        with tqdm(seq, total=total) as pbar:
            for rs in seq:
                yield rs
                pbar.update(len(rs))
    else:
        with tqdm(seq, total=total) as pbar:
            for r in seq:
                yield r
                pbar.update(1)


def _execute(c, inst):
    cmd = inst['cmd']
    if cmd == 'read':
        _read(c, inst['file'], inst['output'], delimiter=inst['delimiter'],
              quotechar=inst['quotechar'], encoding=inst['encoding'],
              fn=inst['fn'])

    elif cmd == 'map':
        if not inst['parallel']:
            _execute_serial_apply(c, inst)
        else:
            _execute_parallel_apply(c, inst)

    # The only place where 'insert' is not used
    elif cmd == 'join':
        _join(c, inst['args'], inst['output'])

    elif cmd == 'mzip':
        gseqs = [groupby(_fetch(c, f"""select * from {table}
                                     order by {', '.join(listify(cols))}"""),
                         _build_keyfn(cols))
                 for table, cols in inst['data']]
        fn = inst['fn']() if _is_thunk(inst['fn']) else inst['fn']
        seq = _flatten(fn(*xs) for xs in
                       tqdm(step(gseqs, stop_short=inst['stop_short'])))
        _insert(c, seq, inst['output'])

    elif cmd == 'concat':
        def gen():
            for inp in inst['inputs']:
                for r in _fetch(c, f"select * from {inp}"):
                    yield r
        _insert(c, tqdm(gen()), inst['output'])
    else:
        raise UnknownCommand(cmd)


def _line_count(fname, encoding, newline):
    # read the number of lines fast
    def blocks(fp):
        while True:
            b = fp.read(65536)
            if not b:
                break
            yield b
    with open(fname, encoding=encoding, newline=newline, errors='ignore') as f:
        # subtract -1 for a header
        return (sum(bl.count("\n") for bl in blocks(f))) - 1


def _execute_serial_apply(c, inst):
    tsize = _size(c, inst['inputs'][0])
    logger.info(f"processing {inst['cmd']}: {inst['output']}")
    seq = _fetch(c, f"select * from {inst['inputs'][0]}", inst['by'])
    evaled_fn = inst['fn']() if _is_thunk(inst['fn']) else inst['fn']
    seq1 = _applyfn(evaled_fn, _tqdm(seq, tsize, inst['by']))
    _insert(c, seq1, inst['output'])


# sqlite3 in osx can't handle multiple connections properly.
# Do not use multiprocessing.Queue. It's too pricy for this work.
def _execute_parallel_apply(c, inst):
    max_workers = psutil.cpu_count(logical=False)
    tsize = _size(c, inst['inputs'][0])
    # Deal with corner cases
    if max_workers < 2 or tsize < 2:
        _execute_serial_apply(c, inst)
        return

    itable = inst['inputs'][0]
    # temporary directory for all the by-products
    tdir = os.path.join(os.getcwd(), _TEMP)
    if not os.path.exists(tdir):
        os.makedirs(tdir)
    evaled_fn = inst['fn']() if _is_thunk(inst['fn']) else inst['fn']
    tcon = 'con' + _random_string(9)
    ttable = "tbl" + _random_string(9)
    breaks = [int(i * tsize / max_workers)
              for i in range(1, max_workers)]

   # perform all the process per partition.
    def _proc(dbfile, cut):
        query = f"""select * from {ttable}
                    where _ROWID_ > {cut[0]} and _ROWID_ <= {cut[1]}
                 """
        with _connect(dbfile) as c1:
            n = cut[1] - cut[0]
            seq = _applyfn(evaled_fn,
                           _tqdm(_fetch(c1, query, by=inst['by']),
                                 n, by=inst['by']))
            try:
                _insert(c1, seq, inst['output'])
            except NoRowToInsert:
                pass

    def _collect_tables(dbfiles):
        succeeded_dbfiles = []
        for dbfile in dbfiles:
            with _connect(dbfile) as c1:
                if inst['output'] in _ls(c1):
                    succeeded_dbfiles.append(dbfile)

        if succeeded_dbfiles == []:
            raise NoRowToInsert

        with _connect(succeeded_dbfiles[0]) as c1:
            # query order is not actually specified
            ocols = _cols(c1, f"select * from {inst['output']}")
        c.cursor().execute(_create_statement(inst['output'], ocols))

        # collect tables from dbfiles
        for dbfile in succeeded_dbfiles:
            c.cursor().execute(f"attach database '{dbfile}' as {tcon}")
            c.cursor().execute(f"""insert into {inst['output']}
                                  select * from {tcon}.{inst['output']}
                               """)
            c.commit()
            c.cursor().execute(f"detach database {tcon}")

    def _delete_dbfiles(dbfiles):
        with _delayed_keyboard_interrupts():
            for dbfile in dbfiles:
                if os.path.exists(dbfile):
                    os.remove(dbfile)

    # condition for parallel work by group
    if inst['by']:
        def new_breaks(breaks, group_breaks):
            index = 0
            result = []
            n = len(breaks)
            for b0 in group_breaks:
                if index >= n:
                    break
                if b0 < breaks[index]:
                    continue
                while breaks[index] <= b0:
                    index += 1
                    if index >= n:
                        break
                result.append(b0)
            return result

        try:
            dbfile0 = os.path.join(tdir, _random_string(10))
            c.cursor().execute(f"attach database '{dbfile0}' as {tcon}")
            c.cursor().execute(f"""create table {tcon}.{ttable} as select * from {itable}
                                  order by {','.join(inst['by'])}
                               """)
            c.commit()
            group_breaks = \
                [list(r.values())[0] for r in c.cursor().execute(
                    f"""select _ROWID_ from {tcon}.{ttable}
                        group by {','.join(inst['by'])} having MAX(_ROWID_)
                    """)]
            if len(group_breaks) == 1:
                _execute_serial_apply(c, inst)
                return
            breaks = new_breaks(breaks, group_breaks)

            dbfiles = [dbfile0] + [os.path.join(tdir, _random_string(10))
                                   for _ in range(len(breaks))]
            exe = Pool(len(dbfiles))

            c.cursor().execute(f"detach database {tcon}")
            logger.info(
                f"processing {inst['cmd']}: {inst['output']}"
                f" (multiprocessing: {len(breaks) + 1})")
            for dbfile in dbfiles[1:]:
                copyfile(dbfiles[0], dbfile)

            exe.map(_proc, dbfiles, zip([0] + breaks, breaks + [tsize]))
            _collect_tables(dbfiles)
        finally:
            _delete_dbfiles(dbfiles)

    # non group parallel work
    else:
        try:
           # remove duplicates
            breaks = list(dict.fromkeys(breaks))
            dbfiles = [os.path.join(tdir, _random_string(10))
                       for _ in range(len(breaks) + 1)]
            exe = Pool(len(dbfiles))
            c.cursor().execute(f"attach database '{dbfiles[0]}' as {tcon}")
            c.cursor().execute(f"""create table {tcon}.{ttable}
                                  as select * from {itable}
                               """)
            c.commit()
            c.cursor().execute(f"detach database {tcon}")
            for dbfile in dbfiles[1:]:
                copyfile(dbfiles[0], dbfile)

            logger.info(
                f"processing {inst['cmd']}: {inst['output']}"
                f" (multiprocessing: {len(breaks) + 1})")

            exe.map(_proc, dbfiles, zip([0] + breaks, breaks + [tsize]))
            _collect_tables(dbfiles)
        finally:
            _delete_dbfiles(dbfiles)


def _ls(c):
    rows = c.cursor().\
        execute("select * from sqlite_master where type='table'")
    return [row['name'] for row in rows]


def _drop(c, tables):
    tables = listify(tables)
    for table in tables:
        if _is_reserved(table):
            raise ReservedKeyword(table)
        c.cursor().execute(f'drop table if exists {table}')


def _random_string(nchars):
    """Generate a random string of lengh 'n' with alphabets and digits."""
    chars = string.ascii_letters + string.digits
    return ''.join(random.SystemRandom().choice(chars)
                   for _ in range(nchars))


# primary keys are too much for non-experts
def _create_statement(name, colnames):
    """Create table if not exists foo (...).

    Note:
        Every type is numeric.
        Table name and column names are all lowercased
    """
    # every col is numeric, this may not be so elegant but simple to handle.
    # If you want to change this, Think again
    schema = ', '.join([col + ' ' + 'numeric' for col in colnames])
    return "create table if not exists %s (%s)" % (name, schema)


# column can contain spaces. So you must strip them all
def _insert_statement(name, d):
    """Generate an insert statememt.

    ex) insert into foo values (:a, :b, :c, ...)
    """
    keycols = ', '.join(":" + c.strip() for c in d)
    return "insert into %s values (%s)" % (name, keycols)


def _read(c, filename, name, delimiter=None, quotechar='"',
          encoding='utf-8', newline="\n", fn=None):
    total = None
    if isinstance(filename, str):
        _, ext = os.path.splitext(filename)
        if ext.lower() == '.xlsx' or ext.lower() == ".xls":
            seq = _read_excel(filename)
        elif ext.lower() == '.sas7bdat':
            seq = _read_sas(filename)
        elif ext.lower() == ".dta":
            seq = _read_stata(filename)
        else:
            # default delimiter is ","
            delimiter = delimiter or\
                ("\t" if ext.lower() == ".tsv" else ",")
            seq = _read_csv(filename, delimiter=delimiter,
                            quotechar=quotechar, encoding=encoding,
                            newline=newline)
            total = _line_count(filename, encoding, newline)
    else:
        # iterator, since you can pass an iterator
        # functions of 'read' should be limited
        seq = filename

    seq = tqdm(seq, total=total)

    if fn:
        seq = _flatten(fn(rs) for rs in seq)
    _insert(c, seq, name)


def _insert(c, rs, name):
    r0, rs = spy(rs)
    if r0 == []:
        raise NoRowToInsert(name)

    cols = list(r0[0])
    for x in [name] + cols:
        if _is_reserved(x):
            raise ReservedKeyword(x)

    try:
        c.cursor().execute(_create_statement(name, cols))
        istmt = _insert_statement(name, r0[0])
        c.cursor().executemany(istmt, rs)

    except sqlite3.OperationalError:
        raise InvalidColumns(cols)


def _read_csv(filename, delimiter=',', quotechar='"',
              encoding='utf-8', newline="\n"):
    with open(filename, encoding=encoding, newline=newline) as f:
        header = [c.strip() for c in f.readline().split(delimiter)]
        yield from csv.DictReader(f, fieldnames=header,
                                  delimiter=delimiter, quotechar=quotechar)


def _read_sas(filename):
    with SAS7BDAT(filename) as f:
        reader = f.readlines()
        header = [c.strip() for c in next(reader)]
        for line in reader:
            yield {k: v for k, v in zip(header, line)}


def _read_df(df):
    cols = df.columns
    header = [c.strip() for c in df.columns]
    for _, r in df.iterrows():
        yield {k: v for k, v in zip(header, ((str(r[c]) for c in cols)))}


# this could be more complex but should it be?
def _read_excel(filename):
    # it's OK. Excel files are small
    df = pd.read_excel(filename, keep_default_na=False)
    yield from _read_df(df)


# raises a deprecation warning
def _read_stata(filename):
    chunk = 10_000
    for xs in pd.read_stata(filename, chunksize=chunk):
        yield from _read_df(xs)


def _is_reserved(x):
    return x.upper() in _RESERVED_KEYWORDS


def _is_thunk(fn):
    return len(signature(fn).parameters) == 0
