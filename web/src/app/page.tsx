import { redirect } from "next/navigation";

// The admin home is the knowledge list for now (memory section arrives in a later
// slice). Middleware guards auth before this runs.
export default function Home() {
  redirect("/knowledge");
}
