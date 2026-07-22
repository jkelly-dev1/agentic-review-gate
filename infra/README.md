# Infrastructure (Azure Container Apps)

`main.bicep` describes an Azure Container Apps deployment for this service:

- a **Log Analytics workspace** backing the Container Apps environment,
- a **Container Apps Environment**,
- a **user-assigned Managed Identity** that (a) pulls the image from **ACR** via
  the `AcrPull` role — no registry passwords — and (b) reads secrets from
  **Key Vault** via the `Key Vault Secrets User` role (get/list),
- a **Key Vault** in RBAC mode holding `github-webhook-secret`,
- the **Container App** itself: external ingress on port 8000, image pulled with
  the MI, and `GITHUB_WEBHOOK_SECRET` wired from a **Key Vault-referenced
  secret**, plus a `/healthz` liveness probe.

## Deploy

> Requires an Azure subscription, an existing ACR with the image pushed, and the
> Azure CLI + Bicep.

```bash
# 1. Build & push the image to your ACR.
az acr login -n <acrName>
docker build -t <acrName>.azurecr.io/agentic-review-gate:latest .
docker push <acrName>.azurecr.io/agentic-review-gate:latest

# 2. Deploy the infrastructure.
az group create -n rg-agentic-review-gate -l eastus
az deployment group create \
  -g rg-agentic-review-gate \
  -f infra/main.bicep \
  -p acrName=<acrName> \
     containerImage=<acrName>.azurecr.io/agentic-review-gate:latest \
     adminObjectId=$(az ad signed-in-user show --query id -o tsv)

# 3. Seed the real webhook secret (the template ships a placeholder value).
az keyvault secret set --vault-name <kvName> \
  --name github-webhook-secret --value "$(openssl rand -hex 32)"
```

The deployment outputs the app FQDN, the Key Vault name, and the identity client id.

## Honesty note

This is **Infrastructure-as-Code demonstrating the Azure Container Apps
pattern** (managed identity for ACR pull + Key Vault-referenced secrets,
environment, ingress, health probe). It has **not** been deployed to a live
Azure subscription, so it is not `az`-validated end to end. Treat resource API
versions and role GUIDs as current-at-authoring and re-check `az bicep build`
against your tenant before a real deploy.
