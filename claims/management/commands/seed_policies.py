"""The book of policyholders the agent verifies callers against.

    python manage.py seed_policies

Fictional people, fictional numbers: every phone is in the +1 555 01xx range
reserved for fiction, so nothing here can dial a real person.
"""

from datetime import date

from django.core.management.base import BaseCommand

from claims.models import Policyholder

BOOK = [
    {
        "policy_number": "PV482193",
        "full_name": "Dana Whitfield",
        "phone": "+15550142887",
        "email": "dana.whitfield@example.com",
        "address": "218 Larkspur Lane, Springfield",
        "coverage": "comprehensive",
        "deductible": 500,
        "roadside_assistance": True,
        "rental_cover": True,
        "renewal_date": date(2027, 3, 14),
        "vehicles": [
            {"year": 2019, "make": "Toyota", "model": "Camry", "colour": "silver", "plate": "8FRT229"}
        ],
        "notes": "Two claims in five years, both weather.",
    },
    {
        "policy_number": "PV771004",
        "full_name": "Marcus Oyelaran",
        "phone": "+15550118432",
        "email": "m.oyelaran@example.com",
        "address": "44 Chapel Street, Riverton",
        "coverage": "collision",
        "deductible": 1000,
        "roadside_assistance": True,
        "rental_cover": False,
        "renewal_date": date(2026, 11, 2),
        "vehicles": [
            {"year": 2021, "make": "Honda", "model": "Civic", "colour": "blue", "plate": "4KLM881"}
        ],
    },
    {
        "policy_number": "PV305518",
        "full_name": "Priya Raghunathan",
        "phone": "+15550193076",
        "email": "priya.r@example.com",
        "address": "9 Riverside Drive, Bellview",
        "coverage": "comprehensive",
        "deductible": 250,
        "roadside_assistance": True,
        "rental_cover": True,
        "renewal_date": date(2027, 1, 30),
        "vehicles": [
            {"year": 2022, "make": "Nissan", "model": "Leaf", "colour": "white", "plate": "7QQP410"},
            {"year": 2015, "make": "Subaru", "model": "Outback", "colour": "grey", "plate": "2WNB663"},
        ],
        "notes": "Two vehicles on one policy.",
    },
    {
        "policy_number": "PV660271",
        "full_name": "Tom Alvarez",
        "phone": "+15550127765",
        "email": "t.alvarez@example.com",
        "address": "1120 Grand Avenue, Northgate",
        "coverage": "liability",
        "deductible": 1000,
        "roadside_assistance": False,
        "rental_cover": False,
        "renewal_date": date(2026, 10, 18),
        "vehicles": [
            {"year": 2017, "make": "Ford", "model": "F-150", "colour": "black", "plate": "5DHT902"}
        ],
        "notes": "Liability only — no tow cover, quote roadside out of pocket.",
    },
    {
        "policy_number": "PV118836",
        "full_name": "Nina Kowalczyk",
        "phone": "+15550176219",
        "email": "nina.k@example.com",
        "address": "63 Mountain Pass Road, Ridgeway",
        "coverage": "comprehensive",
        "deductible": 500,
        "roadside_assistance": True,
        "rental_cover": True,
        "renewal_date": date(2027, 5, 9),
        "vehicles": [
            {"year": 2020, "make": "Mazda", "model": "CX-5", "colour": "silver", "plate": "6JRS118"}
        ],
    },
    {
        "policy_number": "PV904455",
        "full_name": "Omar Haddad",
        "phone": "+15550188341",
        "email": "omar.haddad@example.com",
        "address": "88 Oakwood Lane, Springfield",
        "coverage": "collision",
        "deductible": 750,
        "roadside_assistance": True,
        "rental_cover": False,
        "renewal_date": date(2026, 12, 21),
        "vehicles": [
            {"year": 2018, "make": "Volkswagen", "model": "Golf", "colour": "red", "plate": "3TPK574"}
        ],
    },
    {
        "policy_number": "PV239870",
        "full_name": "Grace Adeyemi",
        "phone": "+15550154028",
        "email": "g.adeyemi@example.com",
        "address": "12 Bellview Terrace, Bellview",
        "coverage": "comprehensive",
        "deductible": 500,
        "roadside_assistance": True,
        "rental_cover": True,
        "renewal_date": date(2027, 2, 6),
        "vehicles": [
            {"year": 2023, "make": "Kia", "model": "Sportage", "colour": "green", "plate": "9XBV347"}
        ],
    },
    {
        "policy_number": "PV557712",
        "full_name": "Ray Castellano",
        "phone": "+15550169503",
        "email": "ray.c@example.com",
        "address": "301 Elm Street, Northgate",
        "coverage": "comprehensive",
        "deductible": 500,
        "roadside_assistance": True,
        "rental_cover": False,
        "status": "lapsed",
        "renewal_date": date(2026, 8, 1),
        "vehicles": [
            {"year": 2016, "make": "BMW", "model": "3 Series", "colour": "black", "plate": "1PGD225"}
        ],
        "notes": "Premium unpaid since August — policy lapsed, do not dispatch.",
    },
    {
        "policy_number": "PV843026",
        "full_name": "Leah Brandt",
        "phone": "+15550135914",
        "email": "leah.brandt@example.com",
        "address": "77 County Road 6, Ridgeway",
        "coverage": "comprehensive",
        "deductible": 250,
        "roadside_assistance": True,
        "rental_cover": True,
        "renewal_date": date(2027, 4, 22),
        "vehicles": [
            {"year": 2021, "make": "Volvo", "model": "V60", "colour": "beige", "plate": "8MCE730"}
        ],
    },
    {
        "policy_number": "PV412699",
        "full_name": "Ivan Petrov",
        "phone": "+15550147260",
        "email": "i.petrov@example.com",
        "address": "5 Grandview Court, Riverton",
        "coverage": "collision",
        "deductible": 1000,
        "roadside_assistance": True,
        "rental_cover": False,
        "renewal_date": date(2026, 9, 30),
        "vehicles": [
            {"year": 2014, "make": "Jeep", "model": "Wrangler", "colour": "red", "plate": "4HNU019"}
        ],
    },
]


class Command(BaseCommand):
    help = "Load the demo book of policyholders the agent verifies against"

    def add_arguments(self, parser):
        parser.add_argument("--clear", action="store_true")

    def handle(self, *args, **options):
        if options["clear"]:
            deleted, _ = Policyholder.objects.all().delete()
            self.stdout.write(f"Deleted {deleted} policyholders")

        for record in BOOK:
            Policyholder.objects.update_or_create(
                policy_number=record["policy_number"], defaults=record
            )

        self.stdout.write(self.style.SUCCESS(f"{len(BOOK)} policyholders on the books"))
        self.stdout.write("\nTry these on a call:")
        for record in BOOK[:3]:
            digits = "".join(c for c in record["phone"] if c.isdigit())
            self.stdout.write(
                f"  {record['policy_number']}  last four {digits[-4:]}  {record['full_name']}"
            )
