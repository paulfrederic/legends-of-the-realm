#!/usr/bin/env python3
"""Generate the three locale pages from index.html.

index.html is both the English source of truth and the English output. The
Dutch and French pages are rendered by loading the source in a real browser
and calling the page's own setLang(), so the body translation goes through
exactly the code path visitors use. Only the head is rewritten in Python:
title, description, canonical, the hreflang cluster, Open Graph and JSON-LD.

Idempotent: safe to re-run over an already-built index.html.
"""
import asyncio, io, json, os, re, shutil, sys, datetime

ROOT = os.path.dirname(os.path.abspath(__file__))
SRC  = os.path.join(ROOT, 'index.html')
BASE = 'https://www.thelegendsoftherealm.com'

# vercel.json sets trailingSlash:false, so locale URLs carry no trailing slash.
# Getting this wrong costs a redirect hop on every hreflang hit.
URL   = {'en': BASE + '/', 'nl': BASE + '/nl', 'fr': BASE + '/fr'}
OUT   = {'en': 'index.html', 'nl': 'nl/index.html', 'fr': 'fr/index.html'}
OGLOC = {'en': 'en_GB', 'nl': 'nl_NL', 'fr': 'fr_FR'}

TITLE = {
 'en': 'Legends of the Realm | Hand-Illustrated 78-Card Tarot Deck',
 'nl': 'Legends of the Realm | Handgeïllustreerd tarotdeck van 78 kaarten',
 'fr': 'Legends of the Realm | Jeu de tarot de 78 cartes illustré à la main',
}
DESC = {
 'en': ('A hand-illustrated 78-card tarot deck with gilded gold edges and a complete '
        'guidebook. First edition, 250 numbered sets. €39.95, free EU shipping.'),
 'nl': ('Een handgeïllustreerd tarotdeck van 78 kaarten met verguld gouden randen en een '
        'volledig handboek. Eerste editie, 250 genummerde sets. €39,95, gratis EU-verzending.'),
 'fr': ('Un jeu de tarot de 78 cartes illustré à la main, tranches dorées et livret complet. '
        'Première édition, 250 exemplaires numérotés. 39,95 €, livraison UE offerte.'),
}
SOCIAL = {
 'en': ('Legends of the Realm: A 78-Card Tarot Deck',
        'Hand-illustrated, gilded gold edges, complete guidebook. First edition, 250 numbered sets. Free EU shipping.'),
 'nl': ('Legends of the Realm: een tarotdeck van 78 kaarten',
        'Handgeïllustreerd, verguld gouden randen, volledig handboek. Eerste editie, 250 genummerde sets. Gratis EU-verzending.'),
 'fr': ('Legends of the Realm : un jeu de tarot de 78 cartes',
        'Illustré à la main, tranches dorées, livret complet. Première édition, 250 exemplaires numérotés. Livraison UE offerte.'),
}
OGALT = {
 'en': 'Three cards from the Legends of the Realm tarot deck fanned against a dark background: The Moon, The Star and The World.',
 'nl': 'Drie kaarten uit het Legends of the Realm tarotdeck tegen een donkere achtergrond: De Maan, De Ster en De Wereld.',
 'fr': "Trois cartes du jeu de tarot Legends of the Realm sur fond sombre : La Lune, L'Étoile et Le Monde.",
}
CRUMB2 = {'en': 'Pre-order the first edition',
          'nl': 'Eerste editie voorbestellen',
          'fr': 'Précommander la première édition'}
PRODNAME = {'en': 'Legends of the Realm: A 78-Card Tarot Deck',
            'nl': 'Legends of the Realm: een tarotdeck van 78 kaarten',
            'fr': 'Legends of the Realm : un jeu de tarot de 78 cartes'}
PRODDESC = {
 'en': ('Hand-illustrated 78-card tarot deck by PsychicWorld. 22 Major Arcana and 56 Minor '
        'Arcana across Cups, Swords, Wands and Pentacles. Premium 310gsm black-core cardstock '
        'with a linen finish and gilded gold edges. Includes a complete guidebook explaining '
        'every card: upright and reversed meanings, the symbolism behind the artwork, and '
        'step-by-step spreads. Available in English, French and Dutch. First edition limited '
        'to 250 hand-numbered sets.'),
 'nl': ('Handgeïllustreerd tarotdeck van 78 kaarten van PsychicWorld. 22 Grote Arcana en 56 '
        'Kleine Arcana verdeeld over Bekers, Zwaarden, Staven en Pentakels. Premium karton van '
        '310 gram met zwarte kern, linnen structuur en verguld gouden randen. Inclusief een '
        'volledig handboek met uitleg bij elke kaart: rechtop en omgekeerd, de symboliek achter '
        'de illustraties en stap voor stap legpatronen. Verkrijgbaar in het Engels, Frans en '
        'Nederlands. Eerste editie beperkt tot 250 handgenummerde sets.'),
 'fr': ("Jeu de tarot de 78 cartes illustré à la main par PsychicWorld. 22 arcanes majeurs et 56 "
        "arcanes mineurs répartis entre Coupes, Épées, Bâtons et Deniers. Carton premium 310 g "
        "à âme noire, finition lin et tranches dorées. Livret complet expliquant chaque carte : "
        "sens à l'endroit et à l'envers, symbolisme des illustrations et tirages pas à pas. "
        "Disponible en anglais, français et néerlandais. Première édition limitée à 250 "
        "exemplaires numérotés à la main."),
}
CARDCAPTION = {'en': 'Hand-illustrated for the Legends of the Realm tarot deck.',
               'nl': 'Handgeïllustreerd voor het Legends of the Realm tarotdeck.',
               'fr': 'Illustré à la main pour le jeu de tarot Legends of the Realm.'}
GALLERYNAME = {'en': 'Legends of the Realm, Major Arcana',
               'nl': 'Legends of the Realm, Grote Arcana',
               'fr': 'Legends of the Realm, arcanes majeurs'}

EU27 = ['AT','BE','BG','CY','CZ','DE','DK','EE','ES','FI','FR','GR','HR','HU','IE',
        'IT','LT','LU','LV','MT','NL','PL','PT','RO','SE','SI','SK']
GALLERY_IDX = [0, 2, 6, 17, 18, 21]


def build_graph(lang, T, slugs):
    """One @graph per locale, with every @id scoped to that locale's URL so the
    three pages never present conflicting claims about the same node."""
    u = URL[lang]
    b = u if u.endswith('/') else u + '/'          # for building #fragments
    def i(frag): return u + '#' + frag
    card = lambda idx: 'cards/lg/%s/major-arcana/%s.jpg' % (lang, slugs[lang][idx])
    thumb = lambda idx: 'cards/sm/%s/major-arcana/%s.jpg' % (lang, slugs[lang][idx])

    org = {"@type": "Organization", "@id": i('organization'), "name": "Consulto B.V.",
           "url": BASE + '/', "logo": {"@id": i('logo')}, "image": {"@id": i('logo')},
           "address": {"@type": "PostalAddress", "addressLocality": "Amsterdam",
                       "addressCountry": "NL"},
           "sameAs": ["https://www.psychicworld.com", "https://paravisie.nl",
                      "https://voyancetchat.fr"]}
    logo = {"@type": "ImageObject", "@id": i('logo'),
            "url": BASE + '/icons/icon-512.png', "contentUrl": BASE + '/icons/icon-512.png',
            "width": 512, "height": 512, "caption": "Legends of the Realm"}
    brand = {"@type": "Brand", "@id": i('brand'), "name": "PsychicWorld",
             "url": "https://www.psychicworld.com"}
    website = {"@type": "WebSite", "@id": i('website'), "url": u,
               "name": "Legends of the Realm", "description": DESC[lang],
               "publisher": {"@id": i('organization')}, "inLanguage": lang}
    offer = {
      "@type": "Offer", "@id": i('offer'), "url": u,
      "price": "39.95", "priceCurrency": "EUR",
      "validFrom": "2026-08-17", "priceValidUntil": "2027-08-17",
      "priceSpecification": {"@type": "UnitPriceSpecification", "price": "39.95",
                             "priceCurrency": "EUR", "valueAddedTaxIncluded": True},
      "availability": "https://schema.org/PreOrder",
      "itemCondition": "https://schema.org/NewCondition",
      "seller": {"@id": i('organization')},
      "shippingDetails": {"@type": "OfferShippingDetails", "@id": i('shipping-eu'),
        "shippingRate": {"@type": "MonetaryAmount", "value": "0", "currency": "EUR"},
        "shippingDestination": {"@type": "DefinedRegion", "addressCountry": EU27}},
      "hasMerchantReturnPolicy": {"@type": "MerchantReturnPolicy", "@id": i('returns'),
        "applicableCountry": EU27,
        "returnPolicyCategory": "https://schema.org/MerchantReturnFiniteReturnWindow",
        "merchantReturnDays": 14,
        "returnMethod": "https://schema.org/ReturnByMail"}}
    product = {
      "@type": "Product", "@id": i('product'), "name": PRODNAME[lang],
      "sku": "LOTR-FE-78", "description": PRODDESC[lang],
      "image": [BASE + '/og-image.jpg'] + [BASE + '/' + card(x) for x in GALLERY_IDX[3:]],
      "brand": {"@id": i('brand')}, "manufacturer": {"@id": i('organization')},
      "category": "Tarot Cards",
      "material": "310gsm black-core cardstock, linen texture finish, gilded gold edges",
      "offers": {"@id": i('offer')},
      "additionalProperty": [
        {"@type": "PropertyValue", "name": "Card count", "value": "78"},
        {"@type": "PropertyValue", "name": "Edition",
         "value": "First edition, limited to 250 hand-numbered sets per language"},
        {"@type": "PropertyValue", "name": "Available languages", "value": "English, French, Dutch"},
        {"@type": "PropertyValue", "name": "Packaging", "value": "Custom rigid telescope box"},
        {"@type": "PropertyValue", "name": "Card edges", "value": "Gilded, gold"}]}
    primary = {"@type": "ImageObject", "@id": i('primaryimage'),
               "url": BASE + '/og-image.jpg', "contentUrl": BASE + '/og-image.jpg',
               "width": 1200, "height": 630, "caption": OGALT[lang]}
    webpage = {"@type": "WebPage", "@id": i('webpage'), "url": u, "name": TITLE[lang],
               "description": DESC[lang], "isPartOf": {"@id": i('website')},
               "about": {"@id": i('product')}, "mainEntity": {"@id": i('product')},
               "primaryImageOfPage": {"@id": i('primaryimage')},
               "inLanguage": lang, "breadcrumb": {"@id": i('breadcrumb')}}
    breadcrumb = {"@type": "BreadcrumbList", "@id": i('breadcrumb'), "itemListElement": [
        {"@type": "ListItem", "position": 1, "name": "Legends of the Realm", "item": u},
        {"@type": "ListItem", "position": 2, "name": CRUMB2[lang]}]}
    faq = {"@type": "FAQPage", "@id": i('faq'), "isPartOf": {"@id": i('webpage')},
           "inLanguage": lang, "mainEntity": [
             {"@type": "Question", "name": T[lang]['faqQ%d' % n],
              "acceptedAnswer": {"@type": "Answer", "text": T[lang]['faqA%d' % n]}}
             for n in range(1, 7)]}
    gallery = {"@type": "ImageGallery", "@id": i('gallery'), "name": GALLERYNAME[lang],
      "isPartOf": {"@id": i('webpage')}, "inLanguage": lang, "associatedMedia": [
        {"@type": "ImageObject", "@id": i('card-' + slugs[lang][x]),
         "name": '%s, Legends of the Realm' % T[lang]['c%dName' % x],
         "caption": '%s: %s. %s' % (T[lang]['c%dName' % x], T[lang]['c%dMean' % x],
                                    CARDCAPTION[lang]),
         "contentUrl": BASE + '/' + card(x), "thumbnailUrl": BASE + '/' + thumb(x),
         "width": 600, "height": 1027, "representativeOfPage": x == 17,
         "creditText": "Consulto B.V.", "copyrightNotice": "© 2026 Consulto B.V.",
         "creator": {"@id": i('organization')}, "license": BASE + '/',
         "acquireLicensePage": BASE + '/'} for x in GALLERY_IDX]}

    return {"@context": "https://schema.org",
            "@graph": [website, webpage, primary, org, logo, brand, product, offer,
                       breadcrumb, faq, gallery]}


def hreflang_block():
    L = ['<link rel="alternate" hreflang="%s" href="%s">' % (l, URL[l]) for l in ('en', 'nl', 'fr')]
    L.append('<link rel="alternate" hreflang="x-default" href="%s">' % URL['en'])
    return '\n'.join(L)


LANG_LINKS = ('<div class="lang-switcher">\n'
              '  <a class="lang-btn%s" href="/" hreflang="en" lang="en">EN</a>\n'
              '  <a class="lang-btn%s" href="/fr" hreflang="fr" lang="fr">FR</a>\n'
              '  <a class="lang-btn%s" href="/nl" hreflang="nl" lang="nl">NL</a>')

OLD_SWITCHER = """<div class="lang-switcher">
  <button class="lang-btn active" onclick="setLang('en')">EN</button>
  <button class="lang-btn" onclick="setLang('fr')">FR</button>
  <button class="lang-btn" onclick="setLang('nl')">NL</button>"""

# <a> needs a couple of properties <button> got for free
LINK_CSS = ('.lang-btn{display:inline-block;text-decoration:none;'
            'font-family:\'Cinzel\',serif;line-height:1;}\n')


def prepare_source(h):
    """Changes shared by all three locales, applied before rendering."""
    if OLD_SWITCHER in h:
        h = h.replace(OLD_SWITCHER, LANG_LINKS % (' active', '', ''), 1)
    if LINK_CSS not in h:
        h = h.replace('\n</style>', '\n' + LINK_CSS + '</style>', 1)
    return h


def rewrite_head(h, lang, T, slugs):
    def sub1(pat, rep, label):
        new, n = re.subn(pat, lambda m: rep, h, count=1, flags=re.S)
        if n != 1:
            sys.exit('%s: matched %d for %s' % (label, n, lang))
        return new

    h = sub1(r'<html lang="[^"]*"', '<html lang="%s"' % lang, 'html lang')
    h = sub1(r'<title>.*?</title>', '<title>%s</title>' % TITLE[lang], 'title')
    h = sub1(r'<meta name="description" content="[^"]*">',
             '<meta name="description" content="%s">' % DESC[lang], 'description')
    h = sub1(r'<link rel="canonical" href="[^"]*">',
             '<link rel="canonical" href="%s">\n%s' % (URL[lang], hreflang_block()), 'canonical')

    ogt, ogd = SOCIAL[lang]
    h = sub1(r'<meta property="og:title" content="[^"]*">',
             '<meta property="og:title" content="%s">' % ogt, 'og:title')
    h = sub1(r'<meta property="og:description" content="[^"]*">',
             '<meta property="og:description" content="%s">' % ogd, 'og:description')
    h = sub1(r'<meta property="og:url" content="[^"]*">',
             '<meta property="og:url" content="%s">' % URL[lang], 'og:url')
    h = sub1(r'<meta property="og:image:alt" content="[^"]*">',
             '<meta property="og:image:alt" content="%s">' % OGALT[lang], 'og:image:alt')
    h = sub1(r'<meta name="twitter:title" content="[^"]*">',
             '<meta name="twitter:title" content="%s">' % ogt, 'tw:title')
    h = sub1(r'<meta name="twitter:description" content="[^"]*">',
             '<meta name="twitter:description" content="%s">' % ogd, 'tw:description')

    alts = [l for l in ('en', 'nl', 'fr') if l != lang]
    oglocs = ('<meta property="og:locale" content="%s">\n' % OGLOC[lang] +
              '\n'.join('<meta property="og:locale:alternate" content="%s">' % OGLOC[a]
                        for a in alts))
    h = sub1(r'<meta property="og:locale" content="[^"]*">\n'
             r'(?:<meta property="og:locale:alternate" content="[^"]*">\n?)*',
             oglocs + '\n', 'og:locale')

    graph = json.dumps(build_graph(lang, T, slugs), indent=2, ensure_ascii=False)
    h = sub1(r'<script type="application/ld\+json">.*?</script>',
             '<script type="application/ld+json">\n%s\n</script>' % graph, 'ld+json')

    h = sub1(r"let CURRENT_LANG = '[a-z]{2}';", "let CURRENT_LANG = '%s';" % lang, 'CURRENT_LANG')

    # active state on the switcher, statically per page
    flags = tuple(' active' if l == lang else '' for l in ('en', 'fr', 'nl'))
    h = re.sub(r'<div class="lang-switcher">\s*'
               r'<a class="lang-btn[^"]*" href="/" hreflang="en" lang="en">EN</a>\s*'
               r'<a class="lang-btn[^"]*" href="/fr" hreflang="fr" lang="fr">FR</a>\s*'
               r'<a class="lang-btn[^"]*" href="/nl" hreflang="nl" lang="nl">NL</a>',
               LANG_LINKS % flags, h, count=1)

    # the checked edition radio is a DOM property, so it does not survive
    # serialization. Put the attribute back on the right one.
    h = re.sub(r'(<input type="radio" name="edition" value="[a-z]{2}")\s+checked', r'\1', h)
    h = h.replace('<input type="radio" name="edition" value="%s"' % lang,
                  '<input type="radio" name="edition" value="%s" checked' % lang, 1)
    return h


SITEMAP = '''<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"
        xmlns:xhtml="http://www.w3.org/1999/xhtml"
        xmlns:image="http://www.google.com/schemas/sitemap-image/1.1">
%s</urlset>
'''

def write_sitemap(T, slugs, lastmod):
    entries = []
    for lang in ('en', 'nl', 'fr'):
        alts = ''.join(
            '    <xhtml:link rel="alternate" hreflang="%s" href="%s"/>\n' % (l, URL[l])
            for l in ('en', 'nl', 'fr'))
        alts += '    <xhtml:link rel="alternate" hreflang="x-default" href="%s"/>\n' % URL['en']
        imgs = '    <image:image>\n      <image:loc>%s/og-image.jpg</image:loc>\n' % BASE
        imgs += '      <image:title>%s</image:title>\n    </image:image>\n' % PRODNAME[lang]
        for x in GALLERY_IDX:
            imgs += ('    <image:image>\n      <image:loc>%s/cards/lg/%s/major-arcana/%s.jpg'
                     '</image:loc>\n      <image:title>%s, Legends of the Realm</image:title>\n'
                     '    </image:image>\n'
                     % (BASE, lang, slugs[lang][x], T[lang]['c%dName' % x]))
        entries.append('  <url>\n    <loc>%s</loc>\n    <lastmod>%s</lastmod>\n'
                       '    <changefreq>weekly</changefreq>\n    <priority>%s</priority>\n%s%s  </url>\n'
                       % (URL[lang], lastmod, '1.0' if lang == 'en' else '0.9', alts, imgs))
    io.open(os.path.join(ROOT, 'sitemap.xml'), 'w', encoding='utf-8').write(SITEMAP % ''.join(entries))


async def render(lang, src_path):
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        b = await p.chromium.launch()
        pg = await b.new_page(viewport={'width': 1280, 'height': 900})
        errs = []
        pg.on('pageerror', lambda e: errs.append(str(e)))
        await pg.goto('file://' + src_path, wait_until='load')
        await pg.wait_for_timeout(1200)
        if lang != 'en':
            await pg.evaluate('setLang(%r)' % lang)
            await pg.wait_for_timeout(600)
        html = await pg.evaluate('document.documentElement.outerHTML')
        await b.close()
    if errs:
        sys.exit('page errors while rendering %s: %s' % (lang, errs))
    return '<!DOCTYPE html>\n' + html


async def main():
    h = io.open(SRC, encoding='utf-8').read()
    shutil.copy2(SRC, SRC + '.pre-locales-' + datetime.datetime.now().strftime('%Y%m%d-%H%M%S'))
    prepared = prepare_source(h)
    tmp = os.path.join(ROOT, '_build_src.html')
    io.open(tmp, 'w', encoding='utf-8').write(prepared)

    T = json.load(io.open('/tmp/T.json', encoding='utf-8'))
    slugs = json.load(io.open('/tmp/slugs.json', encoding='utf-8'))
    lastmod = datetime.date.today().isoformat()

    try:
        for lang in ('en', 'nl', 'fr'):
            page = await render(lang, tmp)
            page = rewrite_head(page, lang, T, slugs)
            out = os.path.join(ROOT, OUT[lang])
            os.makedirs(os.path.dirname(out), exist_ok=True) if os.path.dirname(out) else None
            io.open(out, 'w', encoding='utf-8').write(page)
            print('%-3s -> %-16s %6d bytes' % (lang, OUT[lang], len(page.encode('utf-8'))))
    finally:
        os.remove(tmp)

    write_sitemap(T, slugs, lastmod)
    print('sitemap.xml written, lastmod', lastmod)

if __name__ == '__main__':
    asyncio.run(main())
