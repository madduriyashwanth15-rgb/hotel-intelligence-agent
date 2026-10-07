"""Generic name parts for synthetic guests.

Full names are random first/last combinations; they are not taken from, and do
not describe, any real person or customer.
"""

FIRST_NAMES: tuple[str, ...] = (
    "Aarav", "Aditi", "Akash", "Ananya", "Arjun", "Diya", "Ishaan", "Kavya", "Meera",
    "Neha", "Nikhil", "Pooja", "Priya", "Rahul", "Riya", "Rohan", "Saanvi", "Sanjay",
    "Sneha", "Tanvi", "Varun", "Vikram", "Zara", "Kabir", "Ira", "Dev", "Tara", "Yash",
    "Alex", "Amelia", "Ben", "Chloe", "Daniel", "Ella", "Ethan", "Grace", "Hannah",
    "Jack", "Leah", "Liam", "Maya", "Noah", "Olivia", "Omar", "Sofia", "Lucas", "Mia",
    "Hana", "Kenji", "Lena", "Mateo", "Nadia", "Felix", "Ines", "Yusuf", "Elena",
)  # fmt: skip

LAST_NAMES: tuple[str, ...] = (
    "Agarwal", "Bhat", "Chopra", "Desai", "Gupta", "Iyer", "Joshi", "Kapoor", "Kumar",
    "Menon", "Mehta", "Nair", "Patel", "Pillai", "Rao", "Reddy", "Shah", "Sharma",
    "Singh", "Verma", "Bose", "Das", "Kulkarni", "Mishra", "Banerjee", "Sethi",
    "Brown", "Clarke", "Evans", "Fischer", "Garcia", "Hughes", "Jensen", "Khan",
    "Lopez", "Martin", "Novak", "Okafor", "Rossi", "Silva", "Tanaka", "Walker",
    "Wilson", "Young", "Haddad", "Larsen", "Moreau", "Schmidt", "Costa", "Ito",
)  # fmt: skip

# RFC 2606 reserved domains: guaranteed never to be real mailboxes.
EMAIL_DOMAINS: tuple[str, ...] = ("example.com", "example.org", "example.net")
