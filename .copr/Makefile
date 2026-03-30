srpm:
	dnf -y install python3-devel pyproject-rpm-macros rpmdevtools curl
	curl -LsSf https://astral.sh/uv/install.sh | sh
	export PATH="$$HOME/.local/bin:$$PATH" && \
	spectool -g -R ../arctis-sound-manager.spec && \
	rpmbuild -D "_srcrpmdir $(outdir)" -D "_sourcedir ." -bs ../arctis-sound-manager.spec
