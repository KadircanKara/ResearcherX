# TeX Live scheme-full (~5.7GB) because compilation has no network: anything
# missing from this image is a dead end the user cannot work around, and
# publisher templates (IEEEtran, acmart, elsarticle) pull obscure packages.
#
# PINNED BY DIGEST, not by tag. texlive/texlive publishes only floating tags
# (`latest`, `latest-full`, ...) over a rolling Debian sid base -- there is no
# dated tag to pin. An unannounced base bump already broke this build once:
# sid moved to Python 3.14, `pydantic-core` had no cp314 wheel, and the source
# build failed for want of a C linker. A digest makes the build reproducible
# and turns that class of surprise into a deliberate upgrade.
#
# To upgrade: `docker buildx imagetools inspect texlive/texlive:latest`, take
# the new digest, rebuild, and re-run this task's three isolation checks.
FROM texlive/texlive@sha256:ee8ecc627897eabeb42d862d8187546483455e66ce94aa5c2bce1b45a977ab27

# NO pip install and NO virtualenv: app.py is standard library only. This is a
# security boundary, and ~30 transitive packages to serve two fixed-shape JSON
# endpoints would enlarge exactly the surface this container exists to shrink.
# The base image already ships python3 (3.14 as of this digest).
WORKDIR /srv
COPY app.py ./

# The pinned base ALREADY ships a non-root user at UID 1000
# (`texlive:x:1000:1000::/home/texlive:/bin/bash`), so do NOT create one --
# `useradd --uid 1000` fails with "UID 1000 is not unique". The base's default
# user is root, though, so this USER line is still load-bearing: it is what
# makes the process non-root. The compose file drops capabilities and mounts
# the rootfs read-only on top of it.
USER 1000:1000

# TeX writes caches at runtime -- font caches above all -- and the rootfs is
# read-only, so every writable path must point at the tmpfs. Without this,
# pdflatex mostly survives but xelatex fails on an unwritable font cache,
# which is a broken engine rather than a broken document and would surface
# as "the compiler is broken" to the user. /home/texlive is mode 700 and on
# the read-only layer, so HOME moves too.
#
# This service does NOT offer lualatex (see the comment above _ENGINE_FLAG
# in app.py): widening this block to the whole TEXMF family was tried
# specifically to fix it and did not -- luaotfload still refuses to run for
# reasons unrelated to what's writable here. Do not read this ENV block as
# lualatex support; it exists for pdflatex/xelatex.
ENV HOME=/tmp \
    TMPDIR=/tmp \
    TEXMFHOME=/tmp/texmf-home \
    TEXMFVAR=/tmp/texmf-var \
    TEXMFCONFIG=/tmp/texmf-config \
    TEXMFSYSVAR=/tmp/texmf-sysvar \
    TEXMFSYSCONFIG=/tmp/texmf-sysconfig \
    TEXMFCACHE=/tmp/texmf-cache

EXPOSE 8080
CMD ["python3", "/srv/app.py"]
