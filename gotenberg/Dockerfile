# Custom Gotenberg image with the fonts our spreadsheets use, so LibreOffice
# renders them faithfully instead of substituting fallbacks.

# --- Stage 1: fetch and verify Overpass (not packaged by Debian) -------------
FROM debian:trixie-20261005-slim AS overpass

ARG OVERPASS_VERSION=3.0.5
ARG OVERPASS_SHA256=87d738d647546f95996f8a2ad9e2d4b19f93718e7a1695a3831d2d900d132562

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl unzip \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /tmp/overpass
RUN curl -fsSL -o overpass.zip \
        "https://github.com/RedHatOfficial/Overpass/releases/download/v${OVERPASS_VERSION}/overpass-${OVERPASS_VERSION}.zip" \
    && echo "${OVERPASS_SHA256}  overpass.zip" | sha256sum -c - \
    && unzip -q overpass.zip \
    && mkdir /fonts \
    && cp "Overpass-${OVERPASS_VERSION}"/desktop-fonts/overpass/*.otf \
          "Overpass-${OVERPASS_VERSION}"/desktop-fonts/overpass-mono/*.otf /fonts/

# --- Stage 2: Gotenberg with extra fonts -------------------------------------
FROM gotenberg/gotenberg:8.37.0

USER root

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        fonts-lato \
        fonts-roboto \
        fonts-crosextra-carlito \
        fonts-crosextra-caladea \
    && rm -rf /var/lib/apt/lists/*

COPY --from=overpass --chmod=0644 /fonts/ /usr/share/fonts/truetype/overpass/
RUN fc-cache -f

USER gotenberg
