import { apiGet, apiSend } from "./api";
import type { Project, ProjectDetail, Member, Role, Run } from "./types";

export async function listProjects(): Promise<Project[]> {
  return apiGet<Project[]>("/projects");
}

export async function createProject(body: {
  title: string;
  description?: string | null;
  topic_keywords?: string[];
}): Promise<Project> {
  return (await apiSend<Project>("POST", "/projects", body)) as Project;
}

export async function getProject(id: string): Promise<ProjectDetail> {
  return apiGet<ProjectDetail>(`/projects/${id}`);
}

export async function updateProject(
  id: string,
  body: {
    title?: string;
    description?: string | null;
    topic_keywords?: string[];
  }
): Promise<Project> {
  return (await apiSend<Project>("PATCH", `/projects/${id}`, body)) as Project;
}

export async function deleteProject(id: string): Promise<void> {
  await apiSend<void>("DELETE", `/projects/${id}`);
}

export async function listMembers(id: string): Promise<Member[]> {
  return apiGet<Member[]>(`/projects/${id}/members`);
}

export async function addMember(
  id: string,
  body: { user_id: string; role: Role }
): Promise<Member> {
  return (await apiSend<Member>("POST", `/projects/${id}/members`, body)) as Member;
}

export async function updateMemberRole(
  id: string,
  userId: string,
  body: { role: Role }
): Promise<Member> {
  return (await apiSend<Member>("PATCH", `/projects/${id}/members/${userId}`, body)) as Member;
}

export async function removeMember(id: string, userId: string): Promise<void> {
  await apiSend<void>("DELETE", `/projects/${id}/members/${userId}`);
}

export async function listProjectRuns(
  projectId: string,
  limit = 20,
  offset = 0,
): Promise<Run[]> {
  return apiGet<Run[]>(`/projects/${projectId}/runs?limit=${limit}&offset=${offset}`);
}
