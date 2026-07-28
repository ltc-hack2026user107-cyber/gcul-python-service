import gcul  # type: ignore


class SmartPayEscrow(gcul.Contract):  # type: ignore
    platform_escrow: str

    def initialize(self, platform_escrow_account: str) -> None:
        self.platform_escrow = platform_escrow_account

    def create_order(self, buyer: str, amount: int) -> None:
        self.transfer(buyer, self.platform_escrow, amount, "GBP")

    def order_delivered(self, seller: str, amount: int) -> None:
        self.transfer(self.platform_escrow, seller, amount, "GBP")

    def order_failed(self, buyer: str, amount: int) -> None:
        self.transfer(self.platform_escrow, buyer, amount, "GBP")

    def transfer(self, from_account: str, to_account: str, amount: int, currency: str) -> None:
        pass
