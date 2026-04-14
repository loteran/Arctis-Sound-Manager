REPO_ROOT := $(abspath $(dir $(abspath $(lastword $(MAKEFILE_LIST))))/..)

srpm:
	dnf -y install python3-devel python3-pip python3-installer python3-setuptools rpmdevtools curl
	curl -LsSf https://astral.sh/uv/install.sh | sh
	export PATH="$$HOME/.local/bin:$$PATH" && \
	cd $(REPO_ROOT) && \
	VERSION=$$(grep '^version' pyproject.toml | head -1 | sed 's/.*"\(.*\)".*/\1/') && \
	uv build --wheel && \
	mkdir -p .rpmbuild/SOURCES && \
	curl -Lo .rpmbuild/SOURCES/v$$VERSION.tar.gz \
	    "https://github.com/loteran/Arctis-Sound-Manager/archive/refs/tags/v$$VERSION.tar.gz" && \
	cp dist/*.whl .rpmbuild/SOURCES/ && \
	pip3 download --no-deps --dest .rpmbuild/SOURCES "dbus-next==0.2.3" && \
	sed -i "s/^Version:.*/Version:        $$VERSION/" arctis-sound-manager.spec && \
	rpmbuild -D "_topdir $$PWD/.rpmbuild" -D "_srcrpmdir $(outdir)" -D "_sourcedir $$PWD/.rpmbuild/SOURCES" -bs arctis-sound-manager.spec
