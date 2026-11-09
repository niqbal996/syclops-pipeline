# Local image. Runs the same container/install_syclops.sh that builds the cluster image
# (container/build_cluster_image.sh), so both environments are identical.
#
# Build from the repo root:
#   docker build -f container/Dockerfile -t syclops-pipeline:latest .
ARG BASE_IMAGE=nvidia/cuda:12.4.1-base-ubuntu22.04
FROM ${BASE_IMAGE}

# Let the NVIDIA runtime inject all driver libs (CUDA + OptiX).
ENV NVIDIA_VISIBLE_DEVICES=all \
    NVIDIA_DRIVER_CAPABILITIES=all

COPY container/install_syclops.sh /tmp/pipeline/container/install_syclops.sh
COPY external/syclops /tmp/pipeline/external/syclops
RUN bash /tmp/pipeline/container/install_syclops.sh /opt && rm -rf /tmp/pipeline

ENV PATH=/opt/syclops-venv/bin:$PATH \
    SYCLOPS_BLENDER_DIR=/opt/blender-3.6.1 \
    SYCLOPS_BLENDER_VERSION=3.6.1 \
    PYTHONUNBUFFERED=1

WORKDIR /workspace
CMD ["bash"]
