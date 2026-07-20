from app.db.session import SessionLocal
from app.services.crawler.knu import KnuNoticeCrawler
from app.services.staff_directory import sync_staff_directory


def main() -> None:
    records = KnuNoticeCrawler()._crawl_directory()
    with SessionLocal() as db:
        count = sync_staff_directory(db, records)
        db.commit()
    print(f"staff directory synced: {count}")


if __name__ == "__main__":
    main()
