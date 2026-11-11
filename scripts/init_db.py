"""Create tables and load the LATCHA menu. Safe to run again: it only seeds an empty menu."""
import json, pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from sqlalchemy import select, func
from app.db import Base, SessionLocal, engine
from app import models as m


def seed(db):
    if db.scalar(select(func.count(m.MenuItem.id))):
        return 0
    data = json.loads((pathlib.Path(__file__).parent / "menu_seed.json").read_text())
    n = 0
    for cat, rows in data.items():
        for pos, r in enumerate(rows, start=1):  # position matches assets/menu/<cat>-<pos>.png
            cal, sweet, best = (r[2], r[3], r[4] if len(r) > 4 else 0) if len(r) > 2 else (None, None, 0)
            db.add(m.MenuItem(category=cat, position=pos, name=r[0], description=r[1],
                              calories=cal, sweetness=sweet, best_seller=bool(best)))
            n += 1
    db.commit()
    return n


if __name__ == "__main__":
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        print("Tables ready. Items seeded:", seed(db))
