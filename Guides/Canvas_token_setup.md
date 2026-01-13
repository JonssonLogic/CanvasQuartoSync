# Instructions

## Getting the Canvas API token
1. Login to Canvas
2. Go to Account -> Settings
3. Under "Approved integrations", select "+ New access token"-button
4. Write a Purpose and click "Generate token"
5. Copy the token-string right there and set the environment variables in the next section


## Setting environment variables
To set the environment variable permanently on the computer use PowerShell and the following:

`setx CANVAS_API_TOKEN "PASTE_YOUR_TOKEN_HERE"`

`setx CANVAS_API_URL "PASTE_YOUR_URL_HERE"` 

("https://yourschool.instructure.com")
