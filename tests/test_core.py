import os
# import shutil
import pytest
import tablemap as tm

def d(*args, **kwargs):
    return list(args) + [kwargs]

@pytest.fixture(autouse=True)
def run_around_tests():
    files = ['pytest.db',
             'pytest.gv',
             'pytest.gv.pdf']

    def remfiles():
        for fname in files:
            if os.path.isfile(fname):
                os.remove(fname)
    remfiles()
    yield
    remfiles()


def test_loading_ordinary_csv():
    conn = tm.Conn()
    conn['orders'] = d('read', 'tests/Orders.csv')
    conn.run()
    orders1 = conn.get('orders')
    orders2 = tm.util.readxl('tests/Orders.csv')
    header = next(orders2)
    assert list(orders1[0].keys()) == header

    for a, b in zip(orders1, orders2):
        assert list(a.values()) == b


def test_apply_order_year():
    def year(r):
        r['order_year'] = r['order_date'][:4]
        return r

    conn = tm.Conn()
    conn['orders'] = d('read', 'tests/Orders.csv')
    conn['orders1'] = d('apply', year, 'orders')
    conn.run()

    for r in conn.get('orders1'):
        assert r['order_year'] == int(r['order_date'][:4])


# def test_apply_group1():
#     def size(rs):
#         r0 = rs[0]
#         r0['n'] = len(rs)
#         return r0

#     tm.register(
#         order_items=tm.load('tests/OrderItems.csv'),
#         order_items1=tm.apply(size, 'order_items', by='prod_id'),
#         order_items2=tm.apply(size, 'order_items', by='prod_id, order_item'),
#     )
#     inst['order_items'] = dict(cmd='read', file='tests/OrderItems.csv')
    
#     tm.run()
#     assert len(tm.get('order_items1')) == 7
#     assert len(tm.get('order_items2')) == 16


# def test_join():
#     tm.register(
#         products=tm.load('tests/Products.csv'),
#         vendors=tm.load('tests/Vendors.csv'),
#         products1=tm.join(
#             ['products', '*', 'vend_id'],
#             ['vendors', 'vend_name, vend_country', 'vend_id'],
#         )
#     )
#     tm.run()
#     products1 = tm.get('products1')
#     assert products1[0]['vend_country'] == 'USA'
#     assert products1[-1]['vend_country'] == 'England'


# def test_parallel1():
#     def revenue(r):
#         r['rev'] = r['quantity'] * r['item_price']
#         return r

#     tm.register(
#         items=tm.load('tests/OrderItems.csv'),
#         items1=tm.apply(revenue, 'items'),
#         items2=tm.apply(revenue, 'items', parallel=True),

#     )
#     tm.run()
#     assert tm.get('items1') == tm.get('items2')


# def test_parallel2():
#     def size(rs):
#         r0 = rs[0]
#         r0['n'] = len(rs)
#         return r0

#     tm.register(
#         items=tm.load('tests/OrderItems.csv'),
#         items1=tm.apply(size, 'items', by='prod_id'),
#         items2=tm.apply(size, 'items', by='prod_id', parallel=True),
#     )
#     tm.run()
#     assert tm.get('items1') == tm.get('items2')


# def test_select():
#     def mul2(x, y):
#         return x * y

#     tm.register(
#         items=tm.load('tests/OrderItems.csv'),
#         items1=tm.select("*, mul2(quantity, item_price) as total", 'items',
#             where='item_price > 5'
#         ),
#     )

#     tm.run(
#         create_function={
#             'mul2': [2, mul2],
#         }
#     )
     
#     items1 = tm.get('items1')
#     assert len(items1) == 7
#     assert items1[0]['total'] == 549


# def test_full_join_using_mzip():
#     def combine(xs, ys):
#         if xs:
#             for x in xs:
#                 x['prod_name'] = ys[0]['prod_name']
#                 yield x
#         else:
#             yield {
#                 'order_num': '',
#                 'order_item': '',
#                 'prod_id': ys[0]['prod_id'],
#                 'quantity': '',
#                 'item_price': '',
#                 'prod_name': ys[0]['prod_name']
#                 }


#     tm.register(
#         items = tm.load('tests/OrderItems.csv'),
#         prods = tm.load('tests/Products.csv'),

#         items1 = tm.mzip(combine, [
#             ('items', 'prod_id'),
#             ('prods', 'prod_id')
#         ])
#     )
#     tm.run()

#     items1 = tm.get('items1', df=True) 
#     assert len(items1) == 20 

