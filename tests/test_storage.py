from app.models import AppConfig, Lot, LotSide
from app.storage import Repository


def test_repository_round_trip(tmp_path):
    repository = Repository(tmp_path)
    config = AppConfig(shop_name="ДК 41–91")
    repository.save_config(config)
    assert repository.config().shop_name == "ДК 41–91"

    lot = Lot(name="Кристал", side=LotSide.BUY, price=123, quantity=4, icon_file="x.png")
    repository.add_lot(lot)
    assert repository.lots() == [lot]

    repository.replace_lot(lot.model_copy(update={"price": 456}))
    assert repository.lots()[0].price == 456
    repository.delete_lot(lot.id)
    assert repository.lots() == []
