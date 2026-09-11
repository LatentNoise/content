"""LatentNoise Mailer — the single outbound-email door for every product.

A product never speaks SMTP. It posts a message here and gets an id back;
this service owns the credentials, the retries and the log of what was sent.
"""

__version__ = "0.1.0"
