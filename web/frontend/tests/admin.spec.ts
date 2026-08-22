import { expect, type Page, test } from "@playwright/test"
import { firstSuperuser, firstSuperuserPassword } from "./config.ts"
import { randomEmail, randomPassword } from "./utils/random"
import { createUser } from "./utils/testBootstrap"
import { logInUser } from "./utils/user"

const selectProfession = async (
  page: Page,
  profession = "Doctor / physician",
) => {
  await page.getByRole("combobox", { name: "Profession" }).click()
  await page.getByRole("option", { name: profession }).click()
}

test("Admin page is accessible and shows correct title", async ({ page }) => {
  await page.goto("/admin")
  await expect(page.getByRole("heading", { name: "Users" })).toBeVisible()
  await expect(
    page.getByText("Manage user accounts and permissions"),
  ).toBeVisible()
})

test("Add User button is visible", async ({ page }) => {
  await page.goto("/admin")
  await expect(page.getByRole("button", { name: "Add User" })).toBeVisible()
})

test.describe("Admin user management", () => {
  test("Create a new user successfully", async ({ page }) => {
    await page.goto("/admin")

    const email = randomEmail()
    const password = randomPassword()
    const fullName = "Test User Admin"

    await page.getByRole("button", { name: "Add User" }).click()

    await page.getByPlaceholder("Email").fill(email)
    await page.getByPlaceholder("Full name").fill(fullName)
    await page.getByPlaceholder("Password").first().fill(password)
    await page.getByPlaceholder("Password").last().fill(password)
    await selectProfession(page)

    await page.getByRole("button", { name: "Save" }).click()

    await expect(page.getByText("User created successfully")).toBeVisible()

    await expect(page.getByRole("dialog")).not.toBeVisible()

    const userRow = page.getByRole("row").filter({ hasText: email })
    await expect(userRow).toBeVisible()
  })

  test("Create a superuser", async ({ page }) => {
    await page.goto("/admin")

    const email = randomEmail()
    const password = randomPassword()

    await page.getByRole("button", { name: "Add User" }).click()

    await page.getByPlaceholder("Email").fill(email)
    await page.getByPlaceholder("Password").first().fill(password)
    await page.getByPlaceholder("Password").last().fill(password)
    await selectProfession(page)
    await page.getByLabel("Is superuser?").check()
    await page.getByLabel("Is active?").check()

    await page.getByRole("button", { name: "Save" }).click()

    await expect(page.getByText("User created successfully")).toBeVisible()

    await expect(page.getByRole("dialog")).not.toBeVisible()

    const userRow = page.getByRole("row").filter({ hasText: email })
    await expect(userRow.getByText("Superuser")).toBeVisible()
  })

  test("Edit a user successfully", async ({ page }) => {
    await page.goto("/admin")

    const email = randomEmail()
    const password = randomPassword()
    const originalName = "Original Name"
    const updatedName = "Updated Name"
    const discordHandle = "updated-user"

    await page.getByRole("button", { name: "Add User" }).click()
    await page.getByPlaceholder("Email").fill(email)
    await page.getByPlaceholder("Full name").fill(originalName)
    await page.getByPlaceholder("Password").first().fill(password)
    await page.getByPlaceholder("Password").last().fill(password)
    await selectProfession(page)
    await page.getByRole("button", { name: "Save" }).click()

    await expect(page.getByText("User created successfully")).toBeVisible()
    await expect(page.getByRole("dialog")).not.toBeVisible()

    const userRow = page.getByRole("row").filter({ hasText: email })
    await userRow.getByRole("button").click()

    await page.getByRole("menuitem", { name: "Edit User" }).click()

    await page.getByPlaceholder("Full name").fill(updatedName)
    await page.getByPlaceholder("username").fill(discordHandle)
    await page.getByRole("combobox", { name: "Profession" }).click()
    await page.getByRole("option", { name: "Doctor / physician" }).click()
    await page.getByRole("button", { name: "Save" }).click()

    await expect(page.getByText("User updated successfully")).toBeVisible()
    await expect(page.getByText(updatedName)).toBeVisible()

    await userRow.getByRole("button").click()
    await page.getByRole("menuitem", { name: "Edit User" }).click()

    await expect(page.getByPlaceholder("username")).toHaveValue(discordHandle)
    await expect(page.getByRole("combobox", { name: "Profession" })).toHaveText(
      "Doctor / physician",
    )
    await page.getByRole("button", { name: "Cancel" }).click()
  })

  test("Edit user password requires confirmation", async ({ page }) => {
    await page.goto("/admin")

    const email = randomEmail()
    const password = randomPassword()
    const newPassword = randomPassword()

    await page.getByRole("button", { name: "Add User" }).click()
    await page.getByPlaceholder("Email").fill(email)
    await page.getByPlaceholder("Password").first().fill(password)
    await page.getByPlaceholder("Password").last().fill(password)
    await selectProfession(page)
    await page.getByRole("button", { name: "Save" }).click()

    await expect(page.getByText("User created successfully")).toBeVisible()
    await expect(page.getByRole("dialog")).not.toBeVisible()

    const userRow = page.getByRole("row").filter({ hasText: email })
    await userRow.getByRole("button").click()
    await page.getByRole("menuitem", { name: "Edit User" }).click()

    await page.getByPlaceholder("Password").first().fill(newPassword)
    await page.getByRole("button", { name: "Save" }).click()
    await expect(page.getByText("Please confirm your password")).toBeVisible()

    await page.getByPlaceholder("Password").last().fill("different12345")
    await page.getByRole("button", { name: "Save" }).click()
    await expect(page.getByText("The passwords don't match")).toBeVisible()
  })

  test("User action menu does not expose hard delete", async ({ page }) => {
    await page.goto("/admin")

    const email = randomEmail()
    const password = randomPassword()

    await page.getByRole("button", { name: "Add User" }).click()
    await page.getByPlaceholder("Email").fill(email)
    await page.getByPlaceholder("Password").first().fill(password)
    await page.getByPlaceholder("Password").last().fill(password)
    await selectProfession(page)
    await page.getByRole("button", { name: "Save" }).click()

    await expect(page.getByText("User created successfully")).toBeVisible()

    await expect(page.getByRole("dialog")).not.toBeVisible()

    const userRow = page.getByRole("row").filter({ hasText: email })
    await userRow.getByRole("button").click()

    await expect(
      page.getByRole("menuitem", { name: "Edit User" }),
    ).toBeVisible()
    await expect(page.getByRole("menuitem")).toHaveCount(1)
    await page.keyboard.press("Escape")
    await expect(userRow).toBeVisible()
  })

  test("Cancel user creation", async ({ page }) => {
    await page.goto("/admin")

    await page.getByRole("button", { name: "Add User" }).click()
    await page.getByPlaceholder("Email").fill("test@example.com")

    await page.getByRole("button", { name: "Cancel" }).click()

    await expect(page.getByRole("dialog")).not.toBeVisible()
  })

  test("Email is required and must be valid", async ({ page }) => {
    await page.goto("/admin")

    await page.getByRole("button", { name: "Add User" }).click()

    await page.getByPlaceholder("Email").fill("invalid-email")
    await page.getByPlaceholder("Email").blur()

    await expect(page.getByText("Invalid email address")).toBeVisible()
  })

  test("Password must be at least 8 characters", async ({ page }) => {
    await page.goto("/admin")

    await page.getByRole("button", { name: "Add User" }).click()

    await page.getByPlaceholder("Email").fill(randomEmail())
    await page.getByPlaceholder("Password").first().fill("short")
    await page.getByPlaceholder("Password").last().fill("short")
    await page.getByRole("button", { name: "Save" }).click()

    await expect(
      page.getByText("Password must be at least 8 characters"),
    ).toBeVisible()
  })

  test("Passwords must match", async ({ page }) => {
    await page.goto("/admin")

    await page.getByRole("button", { name: "Add User" }).click()

    await page.getByPlaceholder("Email").fill(randomEmail())
    await page.getByPlaceholder("Password").first().fill(randomPassword())
    await page.getByPlaceholder("Password").last().fill("different12345")
    await page.getByPlaceholder("Password").last().blur()

    await expect(page.getByText("The passwords don't match")).toBeVisible()
  })
})

test.describe("Admin page access control", () => {
  test.use({ storageState: { cookies: [], origins: [] } })

  test("Non-superuser cannot access admin page", async ({ page }) => {
    const email = randomEmail()
    const password = randomPassword()

    await createUser({ email, password })
    await logInUser(page, email, password)

    await page.goto("/admin")

    await expect(page.getByRole("heading", { name: "Users" })).not.toBeVisible()
    await expect(page).not.toHaveURL(/\/admin/)
  })

  test("Superuser can access admin page", async ({ page }) => {
    await logInUser(page, firstSuperuser, firstSuperuserPassword)

    await page.goto("/admin")

    await expect(page.getByRole("heading", { name: "Users" })).toBeVisible()
  })
})
