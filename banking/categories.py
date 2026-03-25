"""
Zapp transaction categorization enums.
Source: Categorisation_strat.pdf
"""
from django.db import models


class ZappPrimaryCategory(models.TextChoices):
    """Zapp primary categories. 15 categories as per product spec."""
    HOUSING_LIVING = "housing_living", "Housing & Living"
    UTILITIES_BILLS = "utilities_bills", "Utilities & Bills"
    GROCERIES_ESSENTIALS = "groceries_essentials", "Groceries & Essentials"
    DINING_CAFES = "dining_cafes", "Dining & Cafes"
    TRANSPORTATION = "transportation", "Transportation"
    SHOPPING = "shopping", "Shopping"
    SUBSCRIPTIONS = "subscriptions", "Subscriptions"
    ENTERTAINMENT_SOCIAL = "entertainment_social", "Entertainment & Social"
    HEALTH_WELLNESS = "health_wellness", "Health & Wellness"
    EDUCATION_CAREER = "education_career", "Education & Career"
    TRAVEL = "travel", "Travel"
    FAMILY_GIFTS = "family_gifts", "Family & Gifts"
    FINANCIAL_TRANSFERS = "financial_transfers", "Financial & Transfers"
    INCOME = "income", "Income"
    MISCELLANEOUS = "miscellaneous", "Miscellaneous / Unclassified"


class ZappSubcategory(models.TextChoices):
    """Zapp subcategories. Optional finer-grained labels."""
    # Groceries & Essentials
    GROCERIES = "groceries", "Groceries"
    HOUSEHOLD_SUPPLIES = "household_supplies", "Household Supplies"
    TOILETRIES = "toiletries", "Toiletries"
    PHARMACY_ESSENTIALS = "pharmacy_essentials", "Pharmacy Essentials"
    # Dining & Cafes
    COFFEE = "coffee", "Coffee"
    TAKEOUT = "takeout", "Takeout"
    FAST_FOOD = "fast_food", "Fast Food"
    RESTAURANTS = "restaurants", "Restaurants"
    DELIVERY = "delivery", "Delivery"
    SNACKS_DESSERT = "snacks_dessert", "Snacks / Dessert"
    # Shopping
    CLOTHING = "clothing", "Clothing"
    TECH = "tech", "Tech"
    HOME_DECOR = "home_decor", "Home Decor"
    PERSONAL_ITEMS = "personal_items", "Personal Items"
    IMPULSE_PURCHASE = "impulse_purchase", "Impulse Purchase"
    GENERAL = "general", "General"
    ELECTRONICS = "electronics", "Electronics"
    # Subscriptions
    STREAMING = "streaming", "Streaming"
    MUSIC = "music", "Music"
    SOFTWARE = "software", "Software"
    PRODUCTIVITY_TOOLS = "productivity_tools", "Productivity Tools"
    SOFTWARE_PRODUCTIVITY = "software_productivity", "Software / Productivity"
    FITNESS_MEMBERSHIP = "fitness_membership", "Fitness Membership"
    CLOUD_STORAGE = "cloud_storage", "Cloud / Storage"
    # Entertainment & Social
    GAMES = "games", "Games"
    NIGHTLIFE = "nightlife", "Nightlife"
    EVENTS = "events", "Events"
    HOBBIES = "hobbies", "Hobbies"
    MOVIES = "movies", "Movies"
    SPORTS = "sports", "Sports"
    LIVE_EVENTS = "live_events", "Live Events"
    THEATER = "theater", "Theater"
    # Transportation
    RIDE_SHARE = "ride_share", "Ride Share"
    GAS = "gas", "Gas"
    PARKING = "parking", "Parking"
    PUBLIC_TRANSIT = "public_transit", "Public Transit"
    CAR_RENTAL = "car_rental", "Car Rental"
    TOLLS = "tolls", "Tolls"
    # Travel
    FLIGHTS = "flights", "Flights"
    HOTELS = "hotels", "Hotels"
    AIRBNB = "airbnb", "Airbnb"
    VACATION_PACKAGES = "vacation_packages", "Vacation Packages"
    # Utilities & Bills
    RENT = "rent", "Rent"
    ELECTRICITY = "electricity", "Electricity"
    INTERNET = "internet", "Internet"
    PHONE = "phone", "Phone"
    INSURANCE = "insurance", "Insurance"
    # Health & Wellness
    DOCTOR = "doctor", "Doctor"
    DENTAL = "dental", "Dental"
    THERAPY = "therapy", "Therapy"
    MEDICATION = "medication", "Medication"
    # Financial & Transfers
    SAVINGS_TRANSFER = "savings_transfer", "Savings Transfer"
    CREDIT_CARD_PAYMENT = "credit_card_payment", "Credit Card Payment"
    PEER_TRANSFER = "peer_transfer", "Peer Transfer"
    INVESTING_TRANSFER = "investing_transfer", "Investing Transfer"
    ATM_CASH = "atm_cash", "ATM / Cash Withdrawal"
    # Income
    PAYROLL = "payroll", "Payroll"
