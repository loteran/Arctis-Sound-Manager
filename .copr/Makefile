srpm:
	dnf -y install python3-devel python3-pip python3-setuptools rpmdevtools curl
	curl -LsSf https://astral.sh/uv/install.sh | sh
	export PATH="$$HOME/.local/bin:$$PATH" && \
	cd .. && \
	uv build --wheel && \
	VERSION=$$(grep '^Version:' arctis-sound-manager.spec | awk '{print $$2}') && \
	mkdir -p .rpmbuild/SOURCES && \
	curl -Lo .rpmbuild/SOURCES/v$$VERSION.tar.gz \
	    "https://github.com/loteran/Arctis-Sound-Manager/archive/refs/tags/v$$VERSION.tar.gz" && \
	cp dist/*.whl .rpmbuild/SOURCES/ && \
	rpmbuild -D "_topdir $$PWD/.rpmbuild" -D "_srcrpmdir $(outdir)" -D "_sourcedir $$PWD/.rpmbuild/SOURCES" -bs arctis-sound-manager.spec
