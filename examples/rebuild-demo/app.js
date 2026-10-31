// Synthetic client for the rebuild demo. Not taken from any real service.
fetch("/api/projects");

fetch("/api/projects", {method: "POST", body: payload});

fetch(`/api/projects/${projectId}`);

fetch("/api/export", {method: chooseMethod()});
