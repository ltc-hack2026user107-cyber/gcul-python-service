from ledger_service import app, escrow_contract

app.config['TESTING'] = True
client = app.test_client()
escrow_contract.orders.clear()
escrow_contract.initialize('1:USR:ESCROW', '1:USR:ADMIN')
client.post('/webhooks/orders/create', json={'order_id':'ORD-202','buyer_account':'1:USR:B3','seller_account':'1:USR:S3','amount':4000,'caller':'1:USR:ADMIN'})
resp = client.post('/webhooks/orders/ORD-202/fund', json={'caller':'1:USR:B3'})
print(resp.status_code)
print(resp.get_data(as_text=True))
