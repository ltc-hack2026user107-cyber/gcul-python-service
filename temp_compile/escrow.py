import gcul


class Escrow(gcul.Contract):
    """Escrow contract for the Universal Ledger contract runtime."""

    def create_order(self, buyer_account_id: str, escrow_account_id: str, amount: int) -> None:
        self.transfer(buyer_account_id, escrow_account_id, amount, "GBP")

    def order_delivered(self, seller_account_id: str, escrow_account_id: str, amount: int) -> None:
        self.transfer(escrow_account_id, seller_account_id, amount, "GBP")

    def order_failed(self, buyer_account_id: str, escrow_account_id: str, amount: int) -> None:
        self.transfer(escrow_account_id, buyer_account_id, amount, "GBP")
