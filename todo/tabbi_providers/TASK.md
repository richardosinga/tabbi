# Task: tabbi_providers — research and write real bookable provider content

## What this is

For each city in `tabbi_content/`, we maintain bookable experience listings.
Each listing is a provider POI file (type: poi, provider: true) grouped under
a category POI (type: poi, provider_category: true).

These are **not** Viator/GetYourGuide listings. They are direct local operators
that Tabbi can contact on behalf of travellers. Every provider file must have
a real phone number or WhatsApp so the concierge team can reach them.

## How a batch file looks

Each batch file contains one city path per line, e.g.:

    southamerica/peru/lima
    southamerica/peru/cusco

## What to do per city

### Step 1: decide which categories make sense

Common categories (only include what genuinely exists for this city):
- Food tours (culinary walks, market tours, street food)
- Cooking classes (local cuisine hands-on)
- Paragliding / adventure (if the city has a known spot)
- Surf lessons (coastal cities only)
- Walking tours (historic centre, neighbourhood-specific)
- Day trips (if the city is a base for something famous)
- Hiking / trekking (mountain cities)
- Bike tours

### Step 2: find real operators — research criteria

**What you're looking for:**
- A local business with its own website (not a listing on Viator, GetYourGuide,
  TripAdvisor, Airbnb Experiences, or similar aggregators)
- A phone number or WhatsApp that a concierge could actually call or message
- Evidence of activity in 2024 or 2025 (recent reviews, updated site, active Instagram)
- Published prices or at least a price range

**Red flags to reject:**
- URL redirects to Viator/GYG/TripAdvisor — reject entirely
- Website copyright year older than 2022 with no other activity signals
- "Contact us" form only, no phone or WhatsApp
- Generic tour aggregator masquerading as a local operator
- Prices that seem inflated vs local market (often a GYG markup signal)

**Where to look:**
- Google Maps: search "[city] [category] tour" — check the "Website" link on
  each listing, not the Google-hosted profile. Avoid listings without a website.
- TripAdvisor: use it to discover operator names, then go directly to their
  own websites. Do not use TripAdvisor booking links.
- Instagram: "[city] food tour", "[city] paragliding" etc. Local operators
  often run Instagram-first. Get the WhatsApp from the bio.
- Local expat forums / travel blogs (2023-2025 dated): these often name
  specific operators with honest reviews.

**Verification checklist before including:**
- [ ] Website is the operator's own domain (not an aggregator subdomain)
- [ ] Phone or WhatsApp is a local mobile number (not a call centre)
- [ ] At least one external signal of activity (Google reviews, Instagram posts,
      TripAdvisor reviews) dated 2024 or later
- [ ] Price range is plausible for the local market

### Step 3: write the category POI

One file per category, in `tabbi_content/<city-path>/<category-slug>.md`:

```yaml
---
title: "Lima Food Tours"
type: poi
tags:
  - things_to_do
  - activities
provider_category: true
snippet: "One-line teaser shown in the suggestion strip."
---

Optional: a short paragraph (2-3 sentences) explaining what kind of
experience this is and why it's worth doing in this city specifically.
```

The `snippet` must be specific to this city — not generic.

### Step 4: write provider POIs

One file per operator, also in `tabbi_content/<city-path>/`:
Filename: `<operator-slug>.md` (kebab-case of operator name)

```yaml
---
title: "Lima Gourmet Company"
type: poi
tags:
  - food-tours
provider: true
price: "from $65 per person"
duration: "3 hours"
phone: "+51 997 599 415"
booking_url: "https://www.limagourmetcompany.com/"
snippet: "One concise sentence: what makes this operator distinctive."
---

2-4 sentences describing what the operator does, who it's for, and
what makes it worth choosing. Mention their physical neighbourhood if
useful. Do not pad; every sentence should add information.
```

**Rules:**
- `tags` must match the category POI's slug exactly
- `phone` must be a direct mobile / WhatsApp number — no call centres
- `booking_url` must go to the operator's own booking or contact page
- `snippet` is shown as a card subtitle — keep it under 120 characters
- Write 2-4 operators per category (3 is ideal)
- Prefer operators with WhatsApp — concierge team works primarily over WhatsApp

### Step 5: verify before committing

For each operator file, confirm:
- The `booking_url` loads (does not 404 or redirect to an aggregator)
- The `phone` number format is correct for the country
- The `price` is in the right currency and order of magnitude

## File layout example (Lima)

```
tabbi_content/southamerica/peru/lima/
  food-tours.md              ← category POI
  lima-gourmet-company.md    ← provider (tags: [food-tours])
  lima-tasty-tours.md        ← provider (tags: [food-tours])
  haku-tours.md              ← provider (tags: [food-tours])
  cooking-classes.md         ← category POI
  skykitchen.md              ← provider (tags: [cooking-classes])
  luchitos-cooking-class.md  ← provider (tags: [cooking-classes])
  paragliding.md             ← category POI
  aeroxtreme.md              ← provider (tags: [paragliding])
  condor-xtreme.md           ← provider (tags: [paragliding])
  surf.md                    ← category POI
  pukana-surf.md             ← provider (tags: [surf])
  rasta-surf.md              ← provider (tags: [surf])
  walking-tours.md           ← category POI
  lima-by-walking.md         ← provider (tags: [walking-tours])
  lima-walking-tour.md       ← provider (tags: [walking-tours])
```

## What NOT to do

- Do not link to Viator, GetYourGuide, TripAdvisor, Booking.com, or any
  aggregator — not even as a fallback
- Do not write providers for categories that don't naturally apply to the city
- Do not invent phone numbers or prices — leave the field out if uncertain
- Do not use `email` instead of `phone` for the primary contact field;
  the concierge needs something actionable on WhatsApp
