IMAGE ?= xavierniu/token-exporter
TAG ?= latest

.PHONY: build push

build:
	docker build -t $(IMAGE):$(TAG) .

push: build
	docker push $(IMAGE):$(TAG)