# Copy of vendor/gerber2ems/Dockerfile plus an arm64 fix. Build: docker build -t gerber2ems -f Dockerfile vendor/gerber2ems
ARG BASE_IMAGE=debian:trixie

FROM $BASE_IMAGE

SHELL ["/bin/bash", "-c"]

RUN apt update \
    && apt -qqy install \
      # OpenEMS
      build-essential cmake git libhdf5-dev libvtk9-dev libboost-all-dev libcgal-dev libtinyxml-dev qtbase5-dev libvtk9-qt-dev python3 python3-venv cython3 pip \
      # gerber2ems
      gerbv \
    && rm -rf /var/lib/apt/lists/*

RUN useradd docker && echo "docker:docker" | chpasswd && mkdir /home/docker && chown -R docker:docker /home/docker

USER docker

ENV HOME="/home/docker" \
    XDG_CONFIG_HOME="/home/docker/.config" \
    XDG_DATA_HOME="/home/docker/.local/share" \
    XDG_BIN_HOME="/home/docker/.local/bin" \
    PATH="/home/docker/.local/bin:$PATH"

WORKDIR /home/docker

RUN echo "Installing openEMS..." \
  && git clone https://github.com/thliebig/openEMS-Project.git \
  && pushd ./openEMS-Project \
  && git checkout a30587728affa4f8451e13819981899bd8ab6b64 \
  && git submodule update --init --recursive \
  && ./update_openEMS.sh ~/opt/openEMS --python \
  && popd

COPY --chown=docker:docker . gerber2ems

# AERIS patch: G2E_WINDOW_MM=WxH renders only that window (from the Gerber origin) instead of the whole board.
# A whole dense board makes openEMS spend hours building its operator.
RUN python3 - <<'PY'
p = "gerber2ems/src/gerber2ems/importer.py"
s = open(p).read()
old = "    subprocess.run(gerbv_command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
new = ('    if os.environ.get("G2E_WINDOW_MM"):\n'
       '        w, h = (float(v) / 25.4 for v in os.environ["G2E_WINDOW_MM"].split("x"))\n'
       '        gerbv_command += ["--origin=0x0", f"--window_inch={w:.4f}x{h:.4f}"]\n') + old
assert s.count(old) == 1
open(p, "w").write(s.replace(old, new))
PY

# triangle (nanomesh dep) has no linux/arm64 wheel and its sdist breaks on py3.13: build it from git
RUN echo "Installing gerber2ems..." \
  && pushd ./gerber2ems \
  && source ~/opt/openEMS/venv/bin/activate \
  && pip install cython setuptools wheel \
  && pip install --no-build-isolation git+https://github.com/drufat/triangle.git \
  && pip install . \
  && mkdir --parents ~/.local/bin \
  && ln -s ~/opt/openEMS/venv/bin/{gerber2ems,ems2paraview,ems2png} ~/.local/bin \
  && popd

ENTRYPOINT ["gerber2ems"]
