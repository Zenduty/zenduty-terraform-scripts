# zenduty-terraform-scripts


Clone the repository and navigate to zenduty-terraform folder

### initialize the provider using 

```hcl
terraform init 
```

### Configure Zenduty API Key

once the provider is initialized you can export zenduty api `or` paste the token in provider block in main.tf


```hcl
export ZENDUTY_API_KEY="your-api-key"
```

### Generating API Key

To generate a new API key, use the following steps:

1. Login to your zenduty account and click on your profile on top right corner (..beside what's new)
2. Click on account and then click on the API keys and then click on the Generate API Key button. 
3. Enter the Name of the API key and click on Create and Copy the API Key

### Usage

Once token API key has been configured go to main.tf file and replace demouser,demouser2 with email address of your account members.then use the below command to create the resources mentioned in the main.tf file in zenduty

```hcl
terraform apply
```

