srpm:
	dnf -y install python3-devel python3-pip python3-setuptools rpmdevtools curl
	curl -LsSf https://astral.sh/uv/install.sh | sh
	export PATH="$$HOME/.local/bin:$$PATH" && \
	cd .. && \
	uv build --wheel && \
	mkdir -p .rpmbuild/SOURCES && \
	cp dist/*.whl .rpmbuild/SOURCES/ && \
	spectool -g -C .rpmbuild/SOURCES -R arctis-sound-manager.spec && \
	rpmbuild -D "_topdir $$PWD/.rpmbuild" -D "_srcrpmdir $(outdir)" -D "_sourcedir $$PWD/.rpmbuild/SOURCES" -bs arctis-sound-manager.spec
