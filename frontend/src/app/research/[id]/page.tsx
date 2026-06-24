import { redirect } from "next/navigation"

interface Params {
  params: Promise<{ id: string }>
}

export default async function ProjectPage({ params }: Params) {
  const { id } = await params
  redirect(`/research/${id}/chat`)
}
