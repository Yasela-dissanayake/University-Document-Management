import { ethers } from "hardhat";

async function main() {
  const [deployer] = await ethers.getSigners();

  console.log("Deploying contracts with the account:", deployer.address);

  const accountBalance = await ethers.provider.getBalance(deployer.address);
  console.log("Account balance:", ethers.formatEther(accountBalance));

  const UniversityRegistry = await ethers.getContractFactory(
    "UniversityRegistry"
  );
  const universityRegistry = await UniversityRegistry.deploy();

  await universityRegistry.waitForDeployment();
  const contractAddress = await universityRegistry.getAddress();

  console.log("UniversityRegistry contract deployed to:", contractAddress);

  const deploymentInfo = {
    contractAddress: contractAddress,
    deployer: deployer.address,
    network: "tenderly",
    deploymentTime: new Date().toISOString(),
  };

  console.log("Deployment info:", JSON.stringify(deploymentInfo, null, 2));
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
