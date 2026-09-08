from app import app


def main():
    with app.test_client() as c:
        # login as admin
        c.post("/login", data={"login": "admin", "password": "admin"}, follow_redirects=True)
        r = c.get("/admin/mindmap/data")
        print("status", r.status_code)
        j = r.get_json() or {}
        print("planets", len(j.get("planets", [])))
        print("equipment", len(j.get("equipment", [])))
        print("links", len(j.get("links", [])))


if __name__ == "__main__":
    main()

