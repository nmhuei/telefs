#!/bin/bash
# TeleFS Safety Publish Script

set -e

echo "🔍 Running Local Tests..."
npm test

echo "✅ Tests Passed!"

# Get current version
current_version=$(node -p "require('./package.json').version")
echo "Current version: $current_version"

read -p "Enter new version (or leave empty to keep $current_version): " new_version

if [ ! -z "$new_version" ]; then
    echo "Updating version to $new_version..."
    # Update package.json
    sed -i "s/\"version\": \".*\"/\"version\": \"$new_version\"/" package.json
    # Update setup.py
    sed -i "s/version=\".*\"/version=\"$new_version\"/" setup.py
    # Update cli.py
    sed -i "s/version=\"TeleFS .*\"/version=\"TeleFS $new_version\"/" telefs/cli.py
fi

final_version=$(node -p "require('./package.json').version")

echo "📦 Preparing release for v$final_version..."

git add .
git commit -m "chore: release v$final_version" || echo "No changes to commit"
git tag -a v$final_version -m "Release v$final_version" || echo "Tag already exists"

echo "🚀 Pushing to GitHub..."
git push origin main
git push origin --tags

echo "🚢 Publishing to NPM..."
npm publish

echo "✨ Successfully Published v$final_version!"
