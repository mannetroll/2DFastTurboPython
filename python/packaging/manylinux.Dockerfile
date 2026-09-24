# Release builders may pin this argument to an approved image digest.
ARG MANYLINUX_IMAGE=quay.io/pypa/manylinux_2_28_x86_64
FROM ${MANYLINUX_IMAGE}

# CUDA 13.1 matches the Windows release. Install the toolkit, never a driver.
RUN curl --fail --location https://developer.download.nvidia.com/compute/cuda/repos/rhel8/x86_64/cuda-rhel8.repo \
      --output /etc/yum.repos.d/cuda.repo \
    && dnf install -y cuda-nvcc-13-1 cuda-cudart-devel-13-1 cuda-nvrtc-devel-13-1 \
      mesa-libGL-devel libX11-devel libpng-devel libglvnd-glx libglvnd-opengl libxcb libXau \
    && dnf clean all
ENV CUDA_HOME=/usr/local/cuda-13.1
ENV PATH=/opt/python/cp313-cp313/bin:/usr/local/cuda-13.1/bin:${PATH}
RUN python -m pip install --no-cache-dir 'build>=1.2,<2' 'setuptools>=80' \
      'auditwheel>=6.4,<7' 'patchelf>=0.14' 'cmake>=3.26' ninja twine
COPY build-manylinux.sh /opt/fastturbo/build-manylinux.sh
ENTRYPOINT ["bash", "/opt/fastturbo/build-manylinux.sh"]
