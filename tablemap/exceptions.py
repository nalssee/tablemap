class TablemapError(Exception):
    pass


class NoRowToInsert(TablemapError):
    "Where there's no row to write to a database"
    pass


class NoRowToWrite(TablemapError):
    "When there's no row to write to a CSV file"
    pass


class InvalidGroup(TablemapError):
    pass


class UnknownConfig(TablemapError):
    pass

class UnknownCommand(TablemapError):
    pass

class ReservedKeyword(TablemapError):
    pass

class GraphvizNotInstalled(TablemapError):
    pass 

class InvalidColumns(TablemapError):
    pass


class TableDuplication(TablemapError):
    pass


class NoSuchTableFound(TablemapError):
    pass


class SkipThisTurn(TablemapError):
    pass
